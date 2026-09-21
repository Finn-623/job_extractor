import json

import httpx
from typer.testing import CliRunner

from job_extractor.manual_curl import ManualCurlResponseError, ManualCurlResult, _decode_json_response, manual_result_to_collection_result, parse_curl, run_manual_curl
from job_extractor.models import Job


LIST="curl 'https://api.test/jobs' -X POST -H 'Content-Type: application/json' -H 'Cookie: sid=abc' --data-raw '{\"pageIndex\":1,\"pageSize\":5}'"
DETAIL="curl 'https://api.test/job?id=demo&abroad=' -H 'Accept: application/json'"

def _client():
    rows=[{"id":str(i),"jobName":f"岗位{i}","workPlace":"深圳"} for i in range(1,6)]
    def handler(request):
        if request.url.path=="/jobs":return httpx.Response(200,json={"data":rows,"page":{"totalCount":5}})
        job_id=request.url.params["id"]
        return httpx.Response(200,json={"data":{"positionInfoList":[{"id":job_id,"jobDuty":"负责系统设计与交付，推动跨团队协作。","jobRequirements":"本科及以上，具备工程实践能力。","workPlace":"深圳"}]}})
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_parse_get_curl_and_job_id_substitution():
    spec=parse_curl(DETAIL)
    assert spec.method=="GET" and spec.headers["Accept"]=="application/json"
    assert spec.render_job_id("42").query_params["id"]=="42"

def test_parse_post_json_headers_and_cookie():
    spec=parse_curl(LIST)
    assert spec.method=="POST" and spec.body=={"pageIndex":1,"pageSize":5}
    assert spec.headers["Cookie"]=="sid=abc"

def test_browser_copy_as_curl_common_options_and_markdown_urls():
    command="""curl '[List](https://api.test/jobs)' \\
-X 'POST' \\
-H 'Origin: [https://portal.test](https://portal.test)' \\
-H 'Referer: [https://portal.test/x](https://portal.test/x)' \\
-H 'Content-Length: 999' \\
-b 'sid=abc' --compressed --data-urlencode 'pageIndex=1'"""
    spec=parse_curl(command)
    assert spec.method=="POST" and spec.url=="https://api.test/jobs"
    assert spec.headers["Origin"]=="https://portal.test" and spec.headers["Referer"]=="https://portal.test/x"
    assert spec.headers["Cookie"]=="sid=abc" and "Content-Length" not in spec.headers
    assert spec.body=="pageIndex=1"

def test_browser_accept_encoding_is_removed_without_losing_business_headers():
    spec=parse_curl("curl 'https://api.test/jobs' -H 'Accept-Encoding: gzip, deflate, br, zstd' -H 'Origin: https://portal.test' -H 'Cookie: sid=abc'")
    assert "Accept-Encoding" not in spec.headers
    assert spec.headers=={"Origin":"https://portal.test","Cookie":"sid=abc"}

def test_request_method_and_data_inference_are_safe():
    assert parse_curl("curl 'https://api.test/x' --request GET").method=="GET"
    assert parse_curl("curl 'https://api.test/x' --data 'a=b'").method=="POST"
    assert parse_curl("curl 'https://api.test/x' -X POST --data 'a=b'").method=="POST"

def test_response_json_decode_supports_utf8_bom_and_gb18030():
    assert _decode_json_response(httpx.Response(200,content=b'\xef\xbb\xbf{"ok": 1}'))=={"ok":1}
    legacy=json.dumps({"title":"岗位"},ensure_ascii=False).encode("gb18030")
    assert _decode_json_response(httpx.Response(200,headers={"content-type":"application/json; charset=gb18030"},content=legacy))["title"]=="岗位"

def test_response_decode_errors_are_structured():
    try:_decode_json_response(httpx.Response(200,content=b'\x94\x95\xff'))
    except ManualCurlResponseError as exc:assert exc.code=="RESPONSE_DECODE_ERROR"
    else:assert False
    try:_decode_json_response(httpx.Response(200,content=b"not json"))
    except ManualCurlResponseError as exc:assert exc.code=="RESPONSE_NOT_JSON"
    else:assert False

def test_batch_merges_list_ids_and_generic_jd_aliases():
    result=run_manual_curl(LIST,DETAIL,client=_client())
    assert result.records_path=="data" and result.total==5 and result.confidence=="HIGH"
    assert len(result.jobs)==5 and result.failed_details==0
    assert result.jobs[0].job_id=="1" and result.jobs[0].responsibilities and result.jobs[0].requirements

def test_list_with_complete_jd_is_list_sufficient_without_detail_curl():
    rows=[{"id":"1","jobName":"岗位1","workPlace":"深圳","jobDuty":"负责系统设计与交付，推动跨团队协作。","jobRequirements":"本科及以上，具备工程实践能力。"},{"id":"2","jobName":"岗位2","workPlace":"深圳","jobDuty":"负责服务稳定性建设与交付。","jobRequirements":"本科及以上，具备工程实践能力。"}]
    client=httpx.Client(transport=httpx.MockTransport(lambda _request:httpx.Response(200,json={"data":rows,"page":{"totalCount":2}})))
    result=run_manual_curl(LIST,None,client=client)
    unified=manual_result_to_collection_result(result,"https://entry.test")
    assert result.detail_strategy=="LIST_SUFFICIENT" and len(result.jobs)==2
    assert unified.metrics.jd_strategy=="LIST_SUFFICIENT" and unified.metrics.details_attempted==0

def test_form_urlencoded_current_page_paginates_and_reports_total_pages():
    command="curl 'https://api.test/jobs' -X POST -H 'Content-Type: application/x-www-form-urlencoded' --data 'isFrompb=true&recruitType=1&pageSize=15&currentPage=1'"
    requested=[];events=[]
    def handler(request):
        page=int(dict(httpx.QueryParams(request.content.decode()))["currentPage"]);requested.append(page)
        start=(page-1)*15;count=min(15,136-start)
        rows=[{"id":str(start+i),"jobName":f"岗位{start+i}","jobDuty":"负责系统设计与交付，推动跨团队协作。","jobRequirements":"本科及以上，具备工程实践能力。"} for i in range(count)]
        return httpx.Response(200,json={"data":rows,"total":136})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=run_manual_curl(command,None,max_jobs=None,client=client,progress_callback=lambda event,**data:events.append((event,data)))
    assert requested==list(range(1,11)) and result.list_fetched==136 and result.unique_jobs==136
    progress=[(data["page"],data["pages"],data["fetched"],data["total"]) for event,data in events if event=="list"]
    assert progress==[(page,10,min(page*15,136),136) for page in range(1,11)]
    assert [(event,data["fetched"],data["unique"]) for event,data in events if event in {"list_complete","list_ready"}]==[("list_complete",136,136),("list_ready",136,136)]


def test_full_batch_paginates_deduplicates_and_isolates_detail_failure():
    pages={
        1:[{"id":"1","jobName":"岗位1","workPlace":"深圳"},{"id":"2","jobName":"岗位2","workPlace":"深圳"},{"id":"3","jobName":"岗位3","workPlace":"深圳"}],
        2:[{"id":"3","jobName":"岗位3","workPlace":"深圳"},{"id":"4","jobName":"岗位4","workPlace":"深圳"},{"id":"5","jobName":"岗位5","workPlace":"深圳"}],
        3:[{"id":"6","jobName":"岗位6","workPlace":"深圳"},{"id":"7","jobName":"岗位7","workPlace":"深圳"}],
    }
    def handler(request):
        if request.url.path=="/jobs":
            return httpx.Response(200,json={"data":pages[int(json.loads(request.content)["pageIndex"])],"page":{"totalCount":7}})
        if request.url.params["id"]=="4":return httpx.Response(500,json={"error":"temporary"})
        return httpx.Response(200,json={"data":{"positionInfoList":[{"jobDuty":"负责系统设计与交付，推动跨团队协作。","jobRequirements":"本科及以上，具备工程实践能力。"}]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=run_manual_curl(LIST,DETAIL,max_jobs=None,client=client)
    assert result.total==7 and result.list_fetched==8 and result.unique_jobs==7
    assert len(result.jobs)==6 and result.failed_details==1 and result.failed_detail_reasons[0][0]=="4"
    assert result.final_status=="PARTIAL" and result.list_termination=="TOTAL_REACHED"
    assert [job.job_id for job in result.jobs]==["1","2","3","5","6","7"]


def test_empty_page_and_page_limit_stop_pagination_safely():
    def empty_handler(request):
        page=int(json.loads(request.content)["pageIndex"])
        rows=[{"id":"1","jobName":"岗位1"}] if page==1 else []
        if request.url.path=="/jobs":return httpx.Response(200,json={"data":rows,"page":{"totalCount":2}})
        return httpx.Response(200,json={"data":{"positionInfoList":[{"jobDuty":"职责足够完整。","jobRequirements":"要求足够完整。"}]}})
    with httpx.Client(transport=httpx.MockTransport(empty_handler)) as client:
        result=run_manual_curl(LIST,DETAIL,max_jobs=None,client=client)
    assert result.list_termination=="EMPTY_PAGE" and result.unique_jobs==1


def test_max_pages_prevents_unbounded_pagination():
    def handler(request):
        if request.url.path=="/jobs":
            page=int(json.loads(request.content)["pageIndex"])
            return httpx.Response(200,json={"data":[{"id":str(page),"jobName":f"岗位{page}"}],"page":{"totalCount":999}})
        return httpx.Response(200,json={"data":{"positionInfoList":[{"jobDuty":"职责足够完整。","jobRequirements":"要求足够完整。"}]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=run_manual_curl(LIST,DETAIL,max_jobs=None,max_pages=2,client=client)
    assert result.list_termination=="MAX_PAGES" and result.unique_jobs==2


def test_transient_detail_error_retries_once_but_client_error_does_not():
    calls={"1":0,"2":0}
    rows=[{"id":str(i),"jobName":f"岗位{i}"} for i in range(1,6)]
    def handler(request):
        if request.url.path=="/jobs":return httpx.Response(200,json={"data":rows,"page":{"totalCount":5}})
        job_id=request.url.params["id"]
        if job_id in calls:
            calls[job_id]+=1
        if job_id=="1" and calls[job_id]==1:return httpx.Response(503)
        if job_id=="2":return httpx.Response(404)
        return httpx.Response(200,json={"data":{"positionInfoList":[{"jobDuty":"职责足够完整。","jobRequirements":"要求足够完整。"}]}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=run_manual_curl(LIST,DETAIL,concurrency=5,client=client)
    assert calls=={"1":2,"2":1}
    assert result.failed_details==1 and len(result.jobs)==4 and result.unique_jobs==5


def test_unified_result_accepts_source_cross_page_duplicates():
    job=Job(job_id="1",job_title="岗位",source_url="https://source.test",full_jd="职责",responsibilities=["职责"],requirements=["要求"])
    manual=ManualCurlResult("data",386,22,"HIGH",[job],0,386,357,[],"COMPLETE","TOTAL_REACHED",1.0,39)
    result=manual_result_to_collection_result(manual,"https://entry.test")
    assert result.status=="COMPLETE" and result.total_expected==386
    assert (result.total_fetched,result.total_unique,result.metrics.duplicate_jobs)==(386,357,29)
    assert result.duplicate_audit["source_cross_page_duplicates"] is True and result.warnings
    partial=ManualCurlResult("data",2,22,"HIGH",[job],1,2,2,[("2","HTTP 500")],"PARTIAL","TOTAL_REACHED",1.0,1)
    partial_result=manual_result_to_collection_result(partial,"https://entry.test")
    assert partial_result.status=="INCOMPLETE" and partial_result.metrics.details_failed==1
    assert partial_result.total_expected==2 and partial_result.total_fetched==2 and partial_result.total_unique==2


def test_explicit_curl_file_bypasses_url_discovery(monkeypatch,tmp_path):
    from job_extractor import cli
    job=Job(job_id="1",job_title="岗位",source_url="https://source.test",full_jd="职责",responsibilities=["职责"],requirements=["要求"])
    manual=ManualCurlResult("data",1,22,"HIGH",[job],0,1,1,[],"COMPLETE","TOTAL_REACHED",0.1,1)
    list_file=tmp_path/"list.txt";detail_file=tmp_path/"detail.txt"
    list_file.write_text(LIST,encoding="utf-8");detail_file.write_text(DETAIL,encoding="utf-8")
    monkeypatch.setattr(cli,"run_manual_curl",lambda *_args,**_kwargs:manual)
    monkeypatch.setattr(cli.default_registry,"detect",lambda _url:(_ for _ in ()).throw(AssertionError("discovery invoked")))
    monkeypatch.setattr(cli,"generate_and_render_reports",lambda result,*_args:"UNIFIED "+result.status)
    output=CliRunner().invoke(cli.app,["https://entry.test","--list-curl-file",str(list_file),"--detail-curl-file",str(detail_file)])
    assert output.exit_code==0 and "完成 ✓" in output.output
