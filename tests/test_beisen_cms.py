import httpx
import pytest

from job_extractor.adapters import BeisenCMSCollector, ZhiyeAdapter, default_registry


URL = "https://cssc.zhiye.com/campus/"
FINGERPRINT = '<link href="https://stc-cms.beisen.com/CmsPortal/107/style.css">'


def list_html(page: int, total: int = 63) -> str:
    start = (page - 1) * 15
    count = min(15, total - start)
    links = "".join(f'<a title="Job {i}" href="/zpdetail/{1000 + i}">Job {i}</a>' for i in range(start, start + count))
    return FINGERPRINT + f"<div>共{total}条记录</div>" + links


DETAIL = """
<h1 id="detial_title">AI Engineer</h1>
<label>工作地点：</label><div>连云港市</div>
<label>需求部门：</label><div>智海院</div>
<div>招聘人数：若干</div><div>发布时间：2026-09-03</div><div>截止时间：-</div>
<h3 class="detailTitle">工作职责：</h3><pre class="newDetail">Build systems</pre>
<h3 class="detailTitle">任职资格：</h3><pre class="newDetail">Know Python</pre>
<a nexturl="/jobad/applyresume?adid=1000">申请</a>
"""
SHELL = '<div id="recruit-mobile-app"></div>'
API_DETAIL = {"Code": 200, "Data": {"JobAdName": "API Engineer", "LocName": "杭州市", "Department": "浙江公司", "HeadCountStr": "若干", "PostDateStr": "2026-09-14", "EndTimeStr": "-", "DutyStr": "Build systems", "RequireStr": "Know Python"}}
KUNLUNXIN_LIST = '<div class="jobList">' + ''.join(f'<a href="/xiangqing?jobId={2000+i}"><h3>Kunlunxin {i}</h3>' for i in range(10)) + '</div><a href="/Campuslist?PageIndex=16">16</a>'
KUNLUNXIN_DETAIL = '<div class="title"><h3>Kunlunxin Engineer</h3></div><div class="sx"> | 校园招聘 | 北京市</div><div class="time">发布时间：2026-08-24</div><h4>工作职责:</h4><p>Build chips</p><h4>任职要求:</h4><p>Know systems</p>'


def test_detection_routes_only_fingerprinted_zhiye(monkeypatch):
    class Response:
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url): return Response(FINGERPRINT)
    monkeypatch.setattr("job_extractor.adapters.beisen_cms.httpx.Client", Client)
    assert default_registry.detect(URL) is BeisenCMSCollector
    Client.get = lambda self, url: Response("<html>legacy</html>")
    assert default_registry.detect(URL) is ZhiyeAdapter


def test_list_parser_extracts_total_and_fifteen_position_ids():
    total, rows = BeisenCMSCollector.parse_list(list_html(1))
    assert total == 63 and len(rows) == 15
    assert rows[0]["position_id"] == "1000" and rows[-1]["position_id"] == "1014"


def test_detail_parser_normalizes_mobile_ssr_jd():
    detail = BeisenCMSCollector.parse_detail(DETAIL)
    collector = BeisenCMSCollector(client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))))
    job = collector._job({"position_id": "1000", "title": "List title", "detail_href": "/zpdetail/1000"}, detail, URL, "https://cssc.m.zhiye.com")
    assert job is not None and job.job_title == "AI Engineer" and job.locations == ["连云港市"]
    assert job.responsibilities == ["Build systems"] and job.requirements == ["Know Python"]
    assert "岗位职责" in (job.full_jd or "") and "任职要求" in (job.full_jd or "")


def test_detail_uses_ssr_before_api_fallback():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text=DETAIL)
    collector = BeisenCMSCollector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    detail = collector._fetch_detail("https://cssc.m.zhiye.com", "1000")
    assert detail["title"] == "AI Engineer" and calls == ["/JobAd/Info"]


def test_shell_uses_lightbolt_api_fallback_and_normalizes_job():
    def handler(request):
        if request.url.path == "/JobAd/Info":
            return httpx.Response(200, text=SHELL)
        assert request.url.path == "/LightBoltAPI/JobAd/Info"
        return httpx.Response(200, json=API_DETAIL)
    collector = BeisenCMSCollector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    detail = collector._fetch_detail("https://gtgroup.m.zhiye.com", "1000")
    job = collector._job({"position_id": "1000", "title": "List title", "detail_href": "/zpdetail/1000"}, detail, "https://gtgroup.zhiye.com/campus/", "https://gtgroup.m.zhiye.com")
    assert job is not None and job.job_title == "API Engineer" and job.locations == ["杭州市"]
    assert job.responsibilities == ["Build systems"] and job.requirements == ["Know Python"]


def test_api_failure_fails_closed():
    def handler(request):
        return httpx.Response(200, text=SHELL) if request.url.path == "/JobAd/Info" else httpx.Response(200, json={"Code": 500})
    collector = BeisenCMSCollector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(Exception, match="DETAIL_API_INVALID_RESPONSE"):
        collector._fetch_detail("https://gtgroup.m.zhiye.com", "1000")


def test_kunlunxin_template_list_and_pagination_shape():
    rows, pages = BeisenCMSCollector.parse_kunlunxin_list(KUNLUNXIN_LIST)
    assert BeisenCMSCollector.is_kunlunxin_template(KUNLUNXIN_LIST)
    assert pages == 16 and len(rows) == 10 and rows[0] == {"position_id": "2000", "title": "Kunlunxin 0", "detail_href": "/xiangqing?jobId=2000"}
    assert (159 + BeisenCMSCollector.KUNLUNXIN_PAGE_SIZE - 1) // BeisenCMSCollector.KUNLUNXIN_PAGE_SIZE == 16


def test_kunlunxin_detail_normalizes_and_fails_closed():
    detail = BeisenCMSCollector.parse_kunlunxin_detail(KUNLUNXIN_DETAIL)
    collector = BeisenCMSCollector(client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))))
    job = collector._job({"position_id": "2000", "title": "List", "detail_href": "/xiangqing?jobId=2000"}, detail, "https://kunlunxin.zhiye.com/Campus", "https://kunlunxin.zhiye.com")
    assert job is not None and job.locations == ["北京市"] and job.responsibilities == ["Build chips"] and job.requirements == ["Know systems"]
    assert collector._job({"position_id": "2000", "title": "List", "detail_href": ""}, {**detail, "requirements": None}, "https://kunlunxin.zhiye.com/Campus", "https://kunlunxin.zhiye.com") is None


def test_pagination_requests_five_pages_for_sixty_three_jobs():
    requested = []
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "cssc.zhiye.com":
            if "PageIndex" not in request.url.params:
                return httpx.Response(200, text=list_html(1))
            page = int(request.url.params["PageIndex"])
            requested.append(page)
            return httpx.Response(200, text=list_html(page))
        return httpx.Response(200, text=DETAIL)
    collector = BeisenCMSCollector(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = collector.collect(URL)
    assert requested == [1, 2, 3, 4, 5]
    assert (result.total_expected, result.total_fetched, result.total_unique, result.status) == (63, 63, 63, "COMPLETE")
