from job_extractor.discovery.detector import GenericApiDetector, _Observation
from job_extractor.discovery.scorer import score_list


def test_region_list_is_rejected():
    payload = {"cr": [{"id": 1, "name": "北京", "pcPath": "/a", "mobilePath": "/b", "seasonType": "campus", "description": "d", "displayOrder": 1}, {"id": 2, "name": "上海", "pcPath": "/a", "mobilePath": "/b", "seasonType": "campus", "description": "d", "displayOrder": 2}]}
    score, _evidence, shape = score_list("https://campus.test/api/web/region/selectAllValidRegions", payload)
    assert "REGION_LIST" in shape["rejection_reasons"] and score < 15


def test_category_and_filter_lists_are_rejected():
    payload = {"data": {"filters": [{"id": "1", "label": "研发"}, {"id": "2", "label": "产品"}]}}
    _score, _evidence, shape = score_list("https://jobs.test/api/filters", payload)
    assert shape["rejection_reasons"] and shape["job_entity_density"] < 0.5


def test_department_tree_is_rejected():
    payload = {"data": [{"id": "1", "label": "研发", "parentId": None}, {"id": "2", "label": "前端", "parentId": "1"}]}
    _score, _evidence, shape = score_list("https://jobs.test/api/departments/structure", payload)
    assert shape["rejection_reasons"] and "LOW_JOB_ENTITY_DENSITY" in shape["rejection_reasons"]


def test_content_and_cooperation_lists_are_rejected():
    payload = {"data": [{"id": 1, "title": "合作", "categoryName": "c", "content": "x", "summary": "s", "urlShort": "u", "contentType": "t"}, {"id": 2, "title": "合作2", "categoryName": "c", "content": "y", "summary": "s", "urlShort": "u", "contentType": "t"}]}
    score, _evidence, shape = score_list("https://campushr.test/api/userfacade/crsCategoryContent/getAllOpenCooperationInfoList", payload)
    assert "CONTENT_LIST" in shape["rejection_reasons"] and score < 15


def test_nested_job_array_is_recognized():
    payload = {"envelope": {"result": {"records": [{"id": "1", "title": "Engineer", "city": "SH"}, {"id": "2", "title": "Designer", "city": "BJ"}]}}}
    score, _evidence, shape = score_list("https://jobs.test/api/feed", payload)
    assert shape["candidate_list_path"] == "envelope.result.records" and shape["job_entity_density"] == 1.0 and score >= 15


def test_mixed_config_and_jobs_response_prefers_jobs():
    payload = {"data": {"filters": [{"id": "f1", "label": "x"}], "jobs": [{"id": 1, "title": "Engineer", "city": "SH"}, {"id": 2, "title": "Designer", "city": "BJ"}]}}
    _score, _evidence, shape = score_list("https://jobs.test/api/search", payload)
    assert shape["candidate_list_path"] == "data.jobs" and shape["job_entity_density"] == 1.0


def test_semantic_job_title_inference_for_job_prefixed_fields():
    payload = {"result": {"infoList": [{"jobcode": "J1", "jobname": "Engineer"}, {"jobcode": "J2", "jobname": "Designer"}]}}
    score, _evidence, shape = score_list("https://careers.test/api/recruit/job/query/list", payload)
    assert shape["job_entity_density"] == 1.0 and not shape["rejection_reasons"] and score >= 15


def test_detail_link_evidence_increases_score():
    base = {"data": [{"id": 1, "title": "Engineer", "city": "SH"}, {"id": 2, "title": "Designer", "city": "BJ"}]}
    linked = {"data": [{"id": 1, "title": "Engineer", "city": "SH", "detailUrl": "/job/1"}, {"id": 2, "title": "Designer", "city": "BJ", "detailUrl": "/job/2"}]}
    base_score, _e, _s = score_list("https://jobs.test/api/list", base)
    linked_score, _e2, _s2 = score_list("https://jobs.test/api/list", linked)
    assert linked_score > base_score


def test_serialized_state_job_source_is_recognized():
    observation = _Observation("https://jobs.test/position", "STATE", {}, {}, {"props": {"pageProps": {"jobList": [{"id": "1", "title": "Engineer", "city": "SH"}, {"id": "2", "title": "Designer", "city": "BJ"}]}}}, "HYDRATION", False, 0)
    candidate = GenericApiDetector._candidate(observation)
    assert candidate.source_type == "SERIALIZED_STATE" and candidate.observed_list_length == 2


class _Node:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    def click(self, timeout=None):
        self.page.clicked.append(self.index)


class _Locator:
    def __init__(self, page):
        self.page = page

    def nth(self, index):
        return _Node(self.page, index)


class TriggerPage:
    def __init__(self, nodes):
        self.nodes = nodes
        self.clicked = []

    def evaluate(self, _script):
        return self.nodes

    def locator(self, _selector):
        return _Locator(self)

    def wait_for_timeout(self, _ms):
        pass


def test_job_page_search_trigger_clicks_only_job_semantic_controls():
    page = TriggerPage([
        {"i": 0, "text": "社会招聘", "href": "#/experienced"},
        {"i": 1, "text": "登录", "href": "#/login"},
        {"i": 2, "text": "全部职位", "href": ""},
        {"i": 3, "text": "筛选", "href": ""},
    ])
    clicked = GenericApiDetector._job_page_search_trigger(page)
    assert set(page.clicked) == {0, 2} and len(clicked) == 2 and "社会招聘" in clicked and "全部职位" in clicked


def test_job_page_filter_control_is_not_triggered():
    page = TriggerPage([{"i": 0, "text": "筛选", "href": ""}, {"i": 1, "text": "职位列表", "href": ""}])
    GenericApiDetector._job_page_search_trigger(page)
    assert page.clicked == [1]


class _ZeroLocator:
    def count(self):
        return 0

    def nth(self, _index):
        return self

    def inner_text(self, timeout=None):
        return ""

    def get_attribute(self, _key):
        return None


class _TimeoutPage:
    def on(self, _event, _callback):
        pass

    def goto(self, *_a, **_k):
        pass

    def wait_for_timeout(self, *_a):
        pass

    def evaluate(self, *_a, **_k):
        return None

    def locator(self, _selector):
        return _TimeoutLocator()


class _TimeoutLocator:
    def count(self):
        return 1

    def nth(self, _index):
        return self

    def inner_text(self, timeout=None):
        return ""

    def first(self):
        return self

    def click(self, timeout=None):
        pass

    def get_attribute(self, _key):
        return None


class _TimeoutBrowser:
    def __init__(self):
        self.page = _TimeoutPage()

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def test_bounded_source_discovery_timeout_returns_artifact():
    detector = GenericApiDetector(browser_factory=lambda **_k: _TimeoutBrowser(), source_budget_seconds=0)
    result = detector.discover("https://jobs.test/position")
    assert "SOURCE_DISCOVERY_TIMEOUT" in result.warnings
    assert result.failure_classification == "SOURCE_DISCOVERY_TIMEOUT"
    assert result.source_url == "https://jobs.test/position"
