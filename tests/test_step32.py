from job_extractor.collectors.generic_browser_api import GenericBrowserApiCollector
from job_extractor.collectors.generic_http import GenericHttpCollector
from job_extractor.discovery.models import ApiCandidate,DiscoveryResult,PaginationDetection
from job_extractor.discovery.scorer import score_list
from job_extractor.planning import CollectionPlan,CollectionPlanBuilder,CollectionPlanValidator


def browser_plan(**updates):
    values=dict(source_url="https://jobs.test/campus/",mode="BROWSER_API",executable=True,
        list_endpoint="https://jobs.test/api/v1/search/job/posts",list_method="POST",pagination_type="OFFSET",
        offset_param="offset",page_param="offset",page_size_param="limit",initial_values={"offset":0,"limit":10,"portal_type":6},
        list_path="data.job_post_list",total_field="data.count",job_id_field="id",job_title_field="title",
        detail_mode="LIST_SUFFICIENT",browser_trigger="AUTO_PAGINATION",confidence="HIGH",observed_endpoints=["https://jobs.test/api/v1/search/job/posts"])
    values.update(updates);return CollectionPlan(**values)


def test_signed_public_candidate_uses_browser_observed_pagination_without_persisting_signature():
    c=ApiCandidate(url="https://jobs.test/api/v1/search/job/posts",method="POST",score=25,confidence="HIGH",replayable=True,
        query_params={"offset":"0","limit":"10","_signature":"[REDACTED]"},safe_request_values={"offset":0,"limit":10,"portal_type":6},request_body_shape={"offset":"int","limit":"int","portal_type":"int"},request_content_type="application/json",
        response_shape={"candidate_list_path":"data.job_post_list","sample_field_names":["id","title","description","requirement"],"total_field":"data.count"},observed_list_length=10,observed_total=764,observed_unique_ids=10,job_entity_density=1.0)
    result=DiscoveryResult(source_url="https://jobs.test/campus/",status="DISCOVERED",candidate_list_apis=[c],probable_list_api=c,detected_pagination=PaginationDetection(pagination_type="OFFSET",page_param="offset",page_size_param="limit",total_field="data.count"))
    plan=CollectionPlanBuilder().build(result);validation=CollectionPlanValidator().validate(plan)
    assert plan.mode=="BROWSER_API" and plan.browser_trigger=="AUTO_PAGINATION" and validation.valid
    assert plan.observed_total==764
    assert "_signature" not in plan.query_values and "[REDACTED]" not in plan.model_dump_json()


def test_nested_modern_ats_fields_normalize_generically():
    raw={"id":"9","title":"Engineer","description":"Build systems\nOwn delivery","requirement":"Python\nTeamwork",
        "city_list":[{"name":"Beijing"}],"job_function":{"name":"Software"},"recruit_type":{"name":"Regular","parent":{"name":"Campus"}},"_generic_detail_url":"https://jobs.test/campus/position/9/detail"}
    job=GenericHttpCollector(browser_plan())._job(raw)
    assert job.job_id=="9" and job.locations==["Beijing"] and job.job_category=="Software"
    assert job.recruitment_type=="Campus" and job.detail_url.endswith("/9/detail")
    assert job.full_jd and job.responsibilities and job.requirements


def test_visible_detail_link_requires_both_id_and_title():
    payload={"data":{"job_post_list":[{"id":"9","title":"Engineer"},{"id":"10","title":"Designer"}]}}
    class Page:
        def evaluate(self,script):return [{"url":"https://jobs.test/campus/position/9/detail","text":"Engineer Beijing"},{"url":"https://jobs.test/campus/position/10/detail","text":"Wrong title"}]
    GenericBrowserApiCollector(browser_plan())._bind_visible_links(Page(),payload)
    rows=payload["data"]["job_post_list"]
    assert rows[0]["_generic_detail_url"].endswith("/9/detail") and "_generic_detail_url" not in rows[1]


def test_recruitment_news_is_not_promoted_as_a_job_list():
    payload={"data":[{"id":1,"title":"Campus recruitment news","description":"Announcement","body":"Article body","tag":"News","createTime":1} for _ in range(7)]}
    score,evidence,shape=score_list("https://company.test/api/campusNews/list",payload)
    assert "CONTENT_PAYLOAD" in shape["rejection_reasons"] and score<10
