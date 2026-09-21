from typer.testing import CliRunner
from job_extractor.cli import app
from job_extractor.models import CollectionResult, CollectionMetrics, Job
from job_extractor.discovery.models import DiscoveryResult,NavigationGraph,ApiCandidate
from job_extractor.cli import can_auto_execute_generic
from job_extractor.planning.models import CollectionPlan,PlanValidation

runner = CliRunner()

def test_version_returns_zero():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output

def test_zhiye_detection(monkeypatch, tmp_path):
    monkeypatch.setattr("job_extractor.adapters.beisen_cms.BeisenCMSCollector.probe", classmethod(lambda cls, url: False))
    called = {"value": False}
    def fake_collect(self, url):
        called["value"] = True
        # runtime.collect_url -> finalize_result rebuilds metrics from adapter
        # attrs and recomputes status; keep them consistent with a real COMPLETE.
        self.list_requests = 1
        self.details_attempted = 0
        self.details_succeeded = 0
        self.details_failed = 0
        self.detail_strategy = "LIST_SUFFICIENT"
        self.elapsed_seconds = 1.0
        m = CollectionMetrics(jd_strategy="LIST_SUFFICIENT", list_requests=1,
                              details_succeeded=0, details_failed=0, elapsed_seconds=1.0)
        return CollectionResult(source_url=url, platform="zhiye", company="Test",
                                status="COMPLETE", total_expected=1, total_fetched=1,
                                total_unique=1,
                                jobs=[Job(job_id="J1", job_title="岗", company="Test",
                                          source_url=url, detail_url=url, apply_url=url,
                                          responsibilities=["r"], requirements=["q"])],
                                metrics=m)
    monkeypatch.setattr("job_extractor.cli.ZhiyeAdapter.collect", fake_collect)
    # STEP91: reports resolve the run dir from config.OUTPUT_ROOT (formal layout),
    # so redirect it too — otherwise this offline stub would write into the real output tree.
    monkeypatch.setattr("job_extractor.config.OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr("job_extractor.cli.OUTPUT_DIR", tmp_path)
    result = runner.invoke(app, ["https://leapmotor1.zhiye.com/campus/jobs"])
    assert called["value"] is True  # proven: the known-adapter path used the stub
    assert result.exit_code == 0
    assert "[1/5] 识别招聘网站" in result.output and "[5/5] 保存结果" in result.output
    assert "正在保存：jobs.json" in result.output
    assert "完成 ✓" in result.output and "✓" in result.output
    assert "抓取失败" not in result.output
    assert "Detected platform" not in result.output and "Adapter:" not in result.output
    assert "Reports:" not in result.output and "Collection complete" not in result.output

def test_generic_detection(monkeypatch,tmp_path):
    monkeypatch.setattr("job_extractor.cli.DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    result = runner.invoke(app, ["https://example.com/jobs"])
    assert result.exit_code == 0
    assert "自动识别失败" in result.output and "可以使用 cURL 兜底" in result.output
    assert "Source intent:" not in result.output and "Plan mode:" not in result.output

def test_interactive_auto_failure_enters_multiline_curl_fallback(monkeypatch,tmp_path):
    from job_extractor import cli
    from job_extractor.manual_curl import ManualCurlResult
    monkeypatch.setattr(cli,"DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    monkeypatch.setattr(cli,"_stdin_is_interactive",lambda:True)
    manual=ManualCurlResult("data",1,22,"HIGH",[Job(job_id="1",job_title="岗位",source_url="https://x.test",full_jd="职责",responsibilities=["职责"],requirements=["要求"])],0,1,1,[],"COMPLETE","TOTAL_REACHED",0.1,1)
    seen=[]
    monkeypatch.setattr(cli,"run_manual_curl",lambda list_curl,detail_curl,**kwargs:(seen.append((list_curl,detail_curl)) or manual))
    monkeypatch.setattr(cli,"generate_and_render_reports",lambda *_args,**_kwargs:"ok")
    content="\ncurl 'https://x.test/list'\nEND\ncurl 'https://x.test/detail?id=1'\nEND\n"
    result=runner.invoke(app,["https://example.com/jobs"],input=content)
    assert result.exit_code==0 and seen
    assert "是否使用 cURL 兜底" in result.output and "已切换至 cURL 兜底" in result.output

def test_interactive_list_sufficient_skips_detail_prompt(monkeypatch,tmp_path):
    from job_extractor import cli
    from job_extractor.manual_curl import ManualCurlResult
    monkeypatch.setattr(cli,"DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    monkeypatch.setattr(cli,"_stdin_is_interactive",lambda:True)
    job=Job(job_id="1",job_title="岗位",source_url="https://x.test",full_jd="职责\n要求",responsibilities=["职责"],requirements=["要求"])
    manual=ManualCurlResult("data",1,22,"HIGH",[job],0,1,1,[],"COMPLETE","TOTAL_REACHED",0.1,1,"LIST_SUFFICIENT")
    calls=[]
    monkeypatch.setattr(cli,"run_manual_curl",lambda _list,detail,**_kwargs:(calls.append(detail) or manual))
    monkeypatch.setattr(cli,"generate_and_render_reports",lambda *_args,**_kwargs:"ok")
    result=runner.invoke(app,["https://example.com/jobs"],input="\ncurl 'https://x.test/list'\nEND\n")
    assert result.exit_code==0 and calls==[None]
    assert "List 已包含完整 JD，跳过 Detail cURL。" in result.output
    assert "请粘贴 Detail / JD cURL" not in result.output

def test_auto_failure_non_tty_does_not_prompt(monkeypatch,tmp_path):
    from job_extractor import cli
    monkeypatch.setattr(cli,"DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    monkeypatch.setattr(cli,"_write_fallback_failure",lambda *_args,**_kwargs:None)
    result=runner.invoke(app,["https://example.com/jobs"])
    assert result.exit_code==0 and "是否使用 cURL 兜底" not in result.output

def test_interactive_response_decode_error_is_rendered_without_traceback(monkeypatch,tmp_path):
    from job_extractor import cli
    from job_extractor.manual_curl import ManualCurlResponseError
    monkeypatch.setattr(cli,"DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    monkeypatch.setattr(cli,"_stdin_is_interactive",lambda:True)
    monkeypatch.setattr(cli,"run_manual_curl",lambda *_args,**_kwargs:(_ for _ in ()).throw(ManualCurlResponseError("RESPONSE_DECODE_ERROR")))
    monkeypatch.setattr(cli,"generate_and_render_reports",lambda *_args,**_kwargs:"ok")
    result=runner.invoke(app,["https://example.com/jobs"],input="\ncurl 'https://x.test/list'\nEND\n")
    assert result.exit_code==0 and "Traceback" not in result.output
    assert "响应数据编码无法解析" in result.output and "✗ [2/5]" in result.output

def test_markdown_url_uses_unified_generic_failure_ui(monkeypatch,tmp_path):
    monkeypatch.setattr("job_extractor.cli.DISCOVERY_OUTPUT_DIR",tmp_path)
    seen=[]
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:(seen.append(url) or DiscoveryResult(source_url=url,status="NOT_FOUND")))
    result=runner.invoke(app,["[Skyworth](https://skyworth.hotjob.cn)"])
    assert result.exit_code==0 and seen and set(seen)=={"https://skyworth.hotjob.cn"}
    assert "Job Extractor" in result.output and "✗ [1/5]" in result.output
    for legacy in ("自动识别招聘网站与 API", "Source intent:", "Navigation terminal:", "Plan confidence:", "Discovery output:"):
        assert legacy not in result.output

def test_generic_portal_hands_off_to_known_adapter(monkeypatch, tmp_path):
    destination="https://app.mokahr.com/campus-recruitment/acme/1"
    discovery=DiscoveryResult(source_url="https://company.test/careers",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[{"entry_type":"CAMPUS","text":"校园招聘","url":destination,"destination_host":"app.mokahr.com"}])
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:discovery)
    def fake_moka(self, url):
        # same finalize_result() consistency rules as test_zhiye_detection
        self.list_requests = 1
        self.details_attempted = 0
        self.details_succeeded = 0
        self.details_failed = 0
        self.detail_strategy = "LIST_SUFFICIENT"
        self.elapsed_seconds = 1.0
        m = CollectionMetrics(jd_strategy="LIST_SUFFICIENT", list_requests=1, elapsed_seconds=1.0)
        return CollectionResult(source_url=url, platform="moka", company="Acme",
                                status="COMPLETE", total_expected=1, total_fetched=1,
                                total_unique=1,
                                jobs=[Job(job_id="J1", job_title="岗", company="Acme",
                                          source_url=url, detail_url=url, apply_url=url,
                                          responsibilities=["r"], requirements=["q"])],
                                metrics=m)
    monkeypatch.setattr("job_extractor.cli.MokaAdapter.collect", fake_moka)
    monkeypatch.setattr("job_extractor.cli.generate_and_render_reports",lambda result,run_directory,adapter_name:f"Handoff collection: {adapter_name}")
    monkeypatch.setattr("job_extractor.cli.DISCOVERY_OUTPUT_DIR",tmp_path)
    result=runner.invoke(app,["https://company.test/careers"])
    assert result.exit_code==0
    assert "[5/5] 保存结果" in result.output and "完成 ✓" in result.output
    assert "抓取失败" not in result.output
    assert "Adapter:" not in result.output and "Collection complete" not in result.output

def test_generic_discovery_cli(monkeypatch,tmp_path):
    monkeypatch.setattr("job_extractor.cli.DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    result=runner.invoke(app,["https://example.com/jobs","--discover"])
    assert result.exit_code==0 and "Generic API Discovery" in result.output and "Status: NOT_FOUND" in result.output
    assert list(tmp_path.glob("*_discovery.json"))

def test_known_adapter_skips_discovery(monkeypatch):
    # Routing probes are integration behavior; keep this CLI unit test offline.
    from job_extractor.adapters.beisen_cms import BeisenCMSCollector
    monkeypatch.setattr(BeisenCMSCollector, "probe", classmethod(lambda cls, url: False))
    result=runner.invoke(app,["https://leapmotor1.zhiye.com/campus/jobs","--discover"])
    assert result.exit_code==0 and "Known adapter exists: ZhiyeAdapter" in result.output

def test_invalid_url_returns_nonzero():
    assert runner.invoke(app, ["abc"]).exit_code != 0

def test_high_valid_generic_plan_auto_executes():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",confidence="HIGH",executable=True)
    navigation=type("Navigation",(),{"graph":NavigationGraph(source_intent="CAMPUS",selected_scope="ALL_JOBS",terminal_reason="HIGH_CONFIDENCE_GENERIC_PLAN")})()
    result=DiscoveryResult(source_url=plan.source_url,status="DISCOVERED",probable_list_api=ApiCandidate(url="https://x/api/jobs",method="GET",score=20,confidence="HIGH",observed_list_length=1,observed_unique_ids=1))
    assert can_auto_execute_generic(plan,PlanValidation(valid=True),navigation,result)

def test_high_valid_source_without_deeper_terminal_auto_executes():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",confidence="HIGH",executable=True)
    navigation=type("Navigation",(),{"graph":NavigationGraph(source_intent="CAMPUS",terminal_reason="NO_RECRUITMENT_TERMINAL",stability_state="STABLE")})()
    result=DiscoveryResult(source_url=plan.source_url,status="DISCOVERED",probable_list_api=ApiCandidate(url="https://x/api/jobs",method="GET",score=20,confidence="HIGH",observed_list_length=2,observed_unique_ids=2))
    assert can_auto_execute_generic(plan,PlanValidation(valid=True),navigation,result)

def test_non_discovered_or_invalid_high_plan_does_not_auto_execute():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",confidence="HIGH",executable=True)
    navigation=type("Navigation",(),{"graph":NavigationGraph(source_intent="CAMPUS",terminal_reason="NO_RECRUITMENT_TERMINAL")})()
    candidate=ApiCandidate(url="https://x/api/jobs",method="GET",score=20,confidence="HIGH",observed_list_length=2,observed_unique_ids=2)
    assert not can_auto_execute_generic(plan,PlanValidation(valid=True),navigation,DiscoveryResult(source_url=plan.source_url,status="NOT_FOUND",probable_list_api=candidate))
    assert not can_auto_execute_generic(plan,PlanValidation(valid=False,errors=["missing field"]),navigation,DiscoveryResult(source_url=plan.source_url,status="DISCOVERED",probable_list_api=candidate))

def test_generic_auto_execution_blocks_ambiguity_and_medium_plans():
    medium=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",confidence="MEDIUM",executable=True)
    ambiguous=type("Navigation",(),{"graph":NavigationGraph(source_intent="UNKNOWN",terminal_reason="SCOPE_SELECTION_REQUIRED",available_scopes=["CAMPUS","SOCIAL"])})()
    result=DiscoveryResult(source_url=medium.source_url,status="DISCOVERED")
    assert not can_auto_execute_generic(medium,PlanValidation(valid=True),ambiguous,result)

def test_generic_auto_execution_blocks_empty_observed_list():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",confidence="HIGH",executable=True)
    navigation=type("Navigation",(),{"graph":NavigationGraph(source_intent="CAMPUS",selected_scope="ALL_JOBS",terminal_reason="HIGH_CONFIDENCE_GENERIC_PLAN")})()
    result=DiscoveryResult(source_url=plan.source_url,status="DISCOVERED")
    assert not can_auto_execute_generic(plan,PlanValidation(valid=True),navigation,result)

def test_unstable_discovery_blocks_auto_execution():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",confidence="HIGH",executable=True)
    navigation=type("Navigation",(),{"graph":NavigationGraph(source_intent="CAMPUS",selected_scope="ALL_JOBS",terminal_reason="HIGH_CONFIDENCE_GENERIC_PLAN",stability_state="UNSTABLE")})()
    candidate=ApiCandidate(url="https://x/api/jobs",method="GET",score=20,confidence="HIGH",observed_list_length=1,observed_unique_ids=1)
    result=DiscoveryResult(source_url=plan.source_url,status="DISCOVERED",probable_list_api=candidate)
    assert not can_auto_execute_generic(plan,PlanValidation(valid=True),navigation,result)
