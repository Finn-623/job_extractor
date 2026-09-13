import typer
from datetime import datetime
from time import perf_counter

from job_extractor import __version__
from job_extractor.adapters import default_registry
# Public aliases retained for callers/tests that monkeypatch adapter classes.
from job_extractor.adapters.zhiye import ZhiyeAdapter
from job_extractor.adapters.moka import MokaAdapter
from job_extractor.config import OUTPUT_DIR, DISCOVERY_OUTPUT_DIR, ensure_directories
from job_extractor.exporters import export_collection_result
from job_extractor.url_utils import normalize_url
from job_extractor.runtime import build_output_filename, collect_url, render_result
from job_extractor.planning import CollectionPlanValidator
from job_extractor.planning.execution_contract import PlanContractError
from job_extractor.models import CollectionResult
from job_extractor.reporting import ReportManager
from job_extractor.discovery import load_reusable_discovery
from job_extractor.discovery.runtime_data import resolve_encrypted_runtime_terminal
from job_extractor.discovery.stability import stable_navigation

app = typer.Typer(add_completion=False, invoke_without_command=True)

def generate_and_render_reports(result,run_directory,adapter_name):
    started=perf_counter();artifacts=ReportManager().generate_reports(result,run_directory)
    result.metrics.export_seconds=perf_counter()-started
    export_collection_result(result,artifacts.json_path)
    return (f"Detected platform: {result.platform}\nAdapter: {adapter_name}\n\nCollection complete.\n\nExpected: {result.total_expected}\nFetched: {result.total_fetched}\nUnique: {result.total_unique}\nStatus: {result.status}\n\nReports:\n"
            f"  JSON: {artifacts.json_path}\n  Excel: {artifacts.excel_path}\n  Markdown: {artifacts.markdown_path}")

def execute_plan_or_report(generic, collection_plan) -> CollectionResult|None:
    try:
        return generic.execute_plan(collection_plan)
    except PlanContractError as exc:
        typer.echo(f"Execution contract rejected plan.\nExecution mode: {exc.execution_mode}\nMissing fields: {', '.join(exc.missing_fields) or '-'}\nReason: {exc.reason}")
        return None

def version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()

def valid_http_url(value: str) -> str:
    try:
        return normalize_url(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

@app.command()
def main(
    url: str = typer.Argument(None, callback=valid_http_url),
    version: bool = typer.Option(False, "--version", callback=version_callback, is_eager=True),
    discover: bool = typer.Option(False,"--discover",help="Observe and report probable recruitment APIs."),
    plan: bool = typer.Option(False,"--plan",help="Discover and write an auditable generic collection plan."),
    collect_generic: bool = typer.Option(False,"--collect-generic",help="Discover, validate, and execute a high-confidence generic plan."),
    reuse_discovery: bool = typer.Option(False,"--reuse-discovery",help="Reuse the latest matching, fresh discovery artifact."),
    scope: str|None = typer.Option(None,"--scope",help="Resolve an ambiguous recruitment scope: campus, social, intern, or all."),
) -> None:
    if url is None:
        raise typer.BadParameter("Missing recruitment page URL.", param_hint="URL")
    if scope and scope.lower() not in {"campus","social","intern","all"}:
        raise typer.BadParameter("Scope must be campus, social, intern, or all.", param_hint="--scope")
    if scope and scope.lower()=="all":scope="ALL_JOBS"
    elif scope:scope=scope.upper()
    ensure_directories()
    adapter = default_registry.detect(url)
    typer.echo("Job Extractor")
    typer.echo(f"Source URL: {url}")
    typer.echo()
    if discover or plan or collect_generic:
        if adapter.platform_name!="generic":
            typer.echo(f"Known adapter exists: {adapter.__name__}")
            typer.echo("Generic collection skipped.")
            return
        generic=adapter();typer.echo("Generic API Discovery")
        reused=load_reusable_discovery(url,DISCOVERY_OUTPUT_DIR) if reuse_discovery else None
        result=reused[0] if reused else generic.discover(url)
        if reused:typer.echo(f"Reused discovery: {reused[1]}")
        navigation,stability=stable_navigation(url,generic.discover,default_registry,scope,initial_result=result,deadline_seconds=75)
        handoff=navigation.handoff;result=navigation.terminal_result
        _render_navigation(navigation)
        typer.echo(f"Initial page type: {result.page_type}")
        _render_recruitment_entries(result)
        filename=build_output_filename(url,datetime.now()).removesuffix(".json")+"_discovery.json"
        output_path=DISCOVERY_OUTPUT_DIR/filename
        if discover and not plan and not collect_generic:output_path.write_text(result.model_dump_json(indent=2),encoding="utf-8")
        if plan or collect_generic:
            collection_plan=generic.build_plan(result)
            plan_path=DISCOVERY_OUTPUT_DIR/(filename.removesuffix("_discovery.json")+"_plan.json")
            plan_path.write_text(collection_plan.model_dump_json(indent=2),encoding="utf-8")
            validation=CollectionPlanValidator().validate(collection_plan)
            typer.echo(f"Plan mode: {collection_plan.mode}\nPlan confidence: {collection_plan.confidence}\nPlan valid: {validation.valid}\nPlan output:\n{plan_path}")
            if not collect_generic:return
            if handoff:
                typer.echo(f"Handoff result: {handoff.adapter_name} <- {handoff.url}")
                collector, collected = collect_url(handoff.url, default_registry.detect_known(handoff.url))
                run_name=build_output_filename(handoff.url,datetime.now()).removesuffix(".json")
                typer.echo(generate_and_render_reports(collected,OUTPUT_DIR/run_name,collector.__class__.__name__));return
            if navigation.graph.terminal_reason=="SCOPE_SELECTION_REQUIRED":
                typer.echo(f"Scope selection required: {', '.join(navigation.graph.available_scopes)}");return
            if not validation.valid:
                typer.echo("Plan requires review. Generic collection stopped.");return
            collected=execute_plan_or_report(generic,collection_plan)
            if collected is None:return
            run_name=build_output_filename(url,datetime.now()).removesuffix(".json")
            typer.echo(generate_and_render_reports(collected,OUTPUT_DIR/run_name,generic.__class__.__name__));return
        top=result.probable_list_api; detail=result.candidate_detail_apis[0] if result.candidate_detail_apis else None
        typer.echo(f"Observed requests: {result.network_summary.observed_requests}")
        typer.echo(f"JSON/XHR candidates: {result.network_summary.json_candidates}")
        typer.echo("\nTop List Candidate:")
        typer.echo(f"{top.method} {top.url}\nScore: {top.score}" if top else "None")
        typer.echo(f"\nPagination: {result.detected_pagination.pagination_type}")
        if result.detected_pagination.observed_change: typer.echo(result.detected_pagination.observed_change)
        typer.echo("\nTop Detail Candidate:")
        typer.echo(f"{detail.method} {detail.url}\nScore: {detail.score}" if detail else "None")
        typer.echo(f"\nStatus: {result.status}\nElapsed: {result.elapsed_seconds:.2f}s\nOutput:\n{output_path}")
        return
    if adapter.platform_name == "generic":
        typer.echo(f"Detected platform: {adapter.platform_name}");typer.echo(f"Adapter: {adapter.__name__}")
        generic=adapter();typer.echo("\nRunning automatic Generic API discovery...")
        result=generic.discover(url);filename=build_output_filename(url,datetime.now()).removesuffix(".json")+"_discovery.json"
        runtime_resolution=resolve_encrypted_runtime_terminal(result,generic.discover_terminal,default_registry) if result.probable_list_api is None else None
        if runtime_resolution:
            entry,terminal,fingerprint=runtime_resolution
            typer.echo(f"Encrypted runtime terminal: {fingerprint.provider} ({', '.join(fingerprint.capabilities)}) -> {entry.url}")
            runtime_plan=generic.build_plan(terminal);runtime_validation=CollectionPlanValidator().validate(runtime_plan)
            if runtime_plan.mode=="BROWSER_RUNTIME_DATA" and runtime_validation.valid:
                source=runtime_plan.runtime_source or {}
                typer.echo(f"Runtime pagination: model={source.get('pagination_model')} total={source.get('total')} limit={source.get('limit')} pages={source.get('page_count')}")
                collected=execute_plan_or_report(generic,runtime_plan)
                if collected is None:return
                run_name=build_output_filename(url,datetime.now()).removesuffix(".json")
                typer.echo(generate_and_render_reports(collected,OUTPUT_DIR/run_name,generic.__class__.__name__));return
        navigation,stability=stable_navigation(url,generic.discover,default_registry,scope,initial_result=result,deadline_seconds=75)
        handoff=navigation.handoff;result=navigation.terminal_result
        _render_navigation(navigation)
        typer.echo(f"Initial page type: {result.page_type}")
        _render_recruitment_entries(result)
        output_path=DISCOVERY_OUTPUT_DIR/filename;output_path.write_text(result.model_dump_json(indent=2),encoding="utf-8")
        if handoff:
            typer.echo(f"Handoff result: {handoff.adapter_name} <- {handoff.url}")
            collector,collected=collect_url(handoff.url,default_registry.detect_known(handoff.url))
            run_name=build_output_filename(handoff.url,datetime.now()).removesuffix(".json")
            typer.echo(generate_and_render_reports(collected,OUTPUT_DIR/run_name,collector.__class__.__name__));return
        if navigation.graph.terminal_reason=="SCOPE_SELECTION_REQUIRED":
            typer.echo(f"Scope selection required: {', '.join(navigation.graph.available_scopes)}");return
        collection_plan=generic.build_plan(result);validation=CollectionPlanValidator().validate(collection_plan)
        typer.echo(f"Discovery status: {result.status}\nPlan mode: {collection_plan.mode}\nPlan confidence: {collection_plan.confidence}\nPlan valid: {validation.valid}")
        if can_auto_execute_generic(collection_plan,validation,navigation,result):
            typer.echo("Generic executable plan found.\nCollecting jobs...")
            collected=execute_plan_or_report(generic,collection_plan)
            if collected is None:return
            run_name=build_output_filename(url,datetime.now()).removesuffix(".json")
            typer.echo(generate_and_render_reports(collected,OUTPUT_DIR/run_name,generic.__class__.__name__));return
        typer.echo("Executable plan available. Run with --collect-generic." if validation.valid else "Plan requires review.")
        typer.echo(f"Discovery output:\n{output_path}")
        return

    typer.echo("Collecting scoped job list...")
    collector, result = collect_url(url, adapter)
    run_name=build_output_filename(url,datetime.now()).removesuffix(".json")
    typer.echo(generate_and_render_reports(result,OUTPUT_DIR/run_name,collector.__class__.__name__))

def _render_recruitment_entries(result) -> None:
    typer.echo(f"Recruitment entries found: {len(result.recruitment_entries)}")
    for index, entry in enumerate(result.recruitment_entries, 1):
        text=entry.text.encode(getattr(typer.get_text_stream("stdout"),"encoding",None) or "utf-8",errors="replace").decode(getattr(typer.get_text_stream("stdout"),"encoding",None) or "utf-8",errors="replace")
        typer.echo(f"Entry {index}: type={entry.entry_type} text={text} url={entry.url} destination host={entry.destination_host} adapter={entry.adapter_name or 'Unknown'}")

def _render_navigation(navigation) -> None:
    graph=navigation.graph
    typer.echo(f"Source intent: {graph.source_intent}")
    typer.echo(f"Navigation terminal: {graph.terminal_reason}")
    if graph.selected_scope:typer.echo(f"Selected scope: {graph.selected_scope}")
    if graph.path_explanation:typer.echo(f"Path explanation: {graph.path_explanation}")
    if graph.rejected_alternatives:typer.echo(f"Rejected alternatives: {'; '.join(graph.rejected_alternatives)}")
    if graph.available_scopes:typer.echo(f"Available scopes: {', '.join(graph.available_scopes)}")
    typer.echo(f"Discovery stability: {graph.stability_state} ({graph.stability_attempts} attempt(s))")
    if graph.stability_reason:typer.echo(f"Stability reason: {graph.stability_reason}")

def can_auto_execute_generic(plan,validation,navigation,result) -> bool:
    graph=navigation.graph
    blocked_warning=any(token in " ".join(result.warnings).upper() for token in ("CAPTCHA","HUMAN_VERIFICATION","LOGIN","CHALLENGE","AUTHENTICATION"))
    candidate=result.probable_list_api
    positive_evidence=bool(candidate and (candidate.observed_list_length or 0)>0 and (candidate.observed_unique_ids or 0)>0)
    return (validation.valid and plan.confidence=="HIGH" and graph.stability_state!="UNSTABLE" and graph.terminal_reason=="HIGH_CONFIDENCE_GENERIC_PLAN"
        and graph.selected_scope in (graph.source_intent,"ALL_JOBS")
        and not graph.available_scopes and not plan.total_conflict and not blocked_warning
        and not any("SENSITIVE" in error.upper() for error in validation.errors) and positive_evidence)
