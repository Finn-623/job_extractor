from typer.testing import CliRunner
from job_extractor.cli import app
from job_extractor.models import CollectionResult
from job_extractor.discovery.models import DiscoveryResult,NavigationGraph,ApiCandidate
from job_extractor.cli import can_auto_execute_generic
from job_extractor.planning.models import CollectionPlan,PlanValidation

runner = CliRunner()

def test_version_returns_zero():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output

def test_zhiye_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "job_extractor.cli.ZhiyeAdapter.collect",
        lambda self, url: CollectionResult(
            source_url=url, platform="zhiye", company="Test", status="COMPLETE"
        ),
    )
    monkeypatch.setattr("job_extractor.cli.OUTPUT_DIR", tmp_path)
    result = runner.invoke(app, ["https://leapmotor1.zhiye.com/campus/jobs"])
    assert result.exit_code == 0
    assert "Detected platform: zhiye" in result.output
    assert "Adapter: ZhiyeAdapter" in result.output

def test_generic_detection(monkeypatch,tmp_path):
    monkeypatch.setattr("job_extractor.cli.DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    result = runner.invoke(app, ["https://example.com/jobs"])
    assert result.exit_code == 0
    assert "Detected platform: generic" in result.output and "Running automatic Generic API discovery" in result.output

def test_generic_portal_hands_off_to_known_adapter(monkeypatch):
    destination="https://app.mokahr.com/campus-recruitment/acme/1"
    discovery=DiscoveryResult(source_url="https://company.test/careers",status="PARTIAL",page_type="RECRUITMENT_PORTAL",recruitment_entries=[{"entry_type":"CAMPUS","text":"校园招聘","url":destination,"destination_host":"app.mokahr.com"}])
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:discovery)
    monkeypatch.setattr("job_extractor.cli.MokaAdapter.collect",lambda self,url: CollectionResult(source_url=url,platform="moka",company="Acme",status="COMPLETE"))
    monkeypatch.setattr("job_extractor.cli.generate_and_render_reports",lambda result,run_directory,adapter_name:f"Handoff collection: {adapter_name}")
    result=runner.invoke(app,["https://company.test/careers"])
    assert result.exit_code==0 and "Handoff result: MokaAdapter" in result.output and "Handoff collection: MokaAdapter" in result.output

def test_generic_discovery_cli(monkeypatch,tmp_path):
    monkeypatch.setattr("job_extractor.cli.DISCOVERY_OUTPUT_DIR",tmp_path)
    monkeypatch.setattr("job_extractor.adapters.generic.GenericAdapter.discover",lambda self,url:DiscoveryResult(source_url=url,status="NOT_FOUND"))
    result=runner.invoke(app,["https://example.com/jobs","--discover"])
    assert result.exit_code==0 and "Generic API Discovery" in result.output and "Status: NOT_FOUND" in result.output
    assert list(tmp_path.glob("*_discovery.json"))

def test_known_adapter_skips_discovery():
    result=runner.invoke(app,["https://leapmotor1.zhiye.com/campus/jobs","--discover"])
    assert result.exit_code==0 and "Known adapter exists: ZhiyeAdapter" in result.output

def test_invalid_url_returns_nonzero():
    assert runner.invoke(app, ["abc"]).exit_code != 0

def test_high_valid_generic_plan_auto_executes():
    plan=CollectionPlan(source_url="https://x/jobs",mode="HTTP_API",confidence="HIGH",executable=True)
    navigation=type("Navigation",(),{"graph":NavigationGraph(source_intent="CAMPUS",selected_scope="ALL_JOBS",terminal_reason="HIGH_CONFIDENCE_GENERIC_PLAN")})()
    result=DiscoveryResult(source_url=plan.source_url,status="DISCOVERED",probable_list_api=ApiCandidate(url="https://x/api/jobs",method="GET",score=20,confidence="HIGH",observed_list_length=1,observed_unique_ids=1))
    assert can_auto_execute_generic(plan,PlanValidation(valid=True),navigation,result)

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
