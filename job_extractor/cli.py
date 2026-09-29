import typer
import inspect
import os
from urllib.parse import urlsplit
import sys
import httpx
from datetime import datetime
from pathlib import Path
from time import perf_counter

from job_extractor import __version__
from job_extractor.adapters import default_registry
# Public aliases retained for callers/tests that monkeypatch adapter classes.
from job_extractor.adapters.zhiye import ZhiyeAdapter
from job_extractor.adapters.moka import MokaAdapter
from job_extractor.config import OUTPUT_DIR, DISCOVERY_OUTPUT_DIR, ensure_directories
from job_extractor.exporters import export_collection_result
from job_extractor.url_utils import normalize_url
from job_extractor.runtime import build_output_filename, collect_url, finalize_result, render_result
from job_extractor.planning import CollectionPlanValidator
from job_extractor.planning.execution_contract import PlanContractError
from job_extractor.models import CollectionResult
from job_extractor.reporting import ReportManager
from job_extractor.progress import ProgressReporter, translate_reason  # STEP92: terminal progress UX (observation-only)
from job_extractor.discovery import load_reusable_discovery
from job_extractor.discovery.runtime_data import resolve_encrypted_runtime_terminal
from job_extractor.discovery.stability import stable_navigation
from job_extractor.manual_curl import DetailCurlRequired, ManualCurlResponseError, PageReplayRejected, manual_result_to_collection_result, parse_curl, run_manual_curl
from job_extractor.har_importer import HarImportResult, import_har
from job_extractor.step94h_debug import initialize as initialize_step94h_debug

app = typer.Typer(add_completion=False, invoke_without_command=True)

def generate_and_render_reports(result,run_directory,adapter_name,reporter=None):
    started=perf_counter()
    if reporter is not None:  # spec N: per-file save state for stage [5/5]
        names=["error_report.json"] if result.status=="FAILED" else ["jobs.json","jobs.xlsx","jobs.csv","collection.json","report.md"]
        for nm in names:reporter.save_line(nm)
    # STEP91: the formal layout ignores the legacy domain-slug run name and
    # resolves <OUTPUT_DIR>/<Company>/<stamp>/ (or _unknown/_failed) itself.
    collection_mode=("manual_curl" if "manual" in adapter_name.lower()
                     else "generic" if "generic" in adapter_name.lower() else "provider")
    artifacts=ReportManager().generate_reports(result,None,collection_mode=collection_mode)
    if reporter is not None:reporter.artifacts=artifacts  # STEP92: summary needs the run directory + files
    result.metrics.export_seconds=perf_counter()-started
    if result.status!="FAILED":  # FAILED runs record only error_report.json (STEP91).
        export_collection_result(result,artifacts.json_path)
    return (f"Detected platform: {result.platform}\nAdapter: {adapter_name}\n\nCollection complete.\n\nExpected: {result.total_expected}\nFetched: {result.total_fetched}\nUnique: {result.total_unique}\nStatus: {result.status}\n\nReports:\n"
            f"  JSON: {artifacts.json_path}\n  Excel: {artifacts.excel_path}\n  Markdown: {artifacts.markdown_path}")

def _failure_stage(result) -> tuple[int,str]:
    """STEP92: map the first recorded error (plain string) to a display stage + code."""
    errors=list(getattr(result,"errors",None) or [])
    first=str(errors[0]) if errors else ""
    code=first.split(" ",1)[0].split(":",1)[0] if first else "UNKNOWN"
    upper=first.upper()
    if "DISCOVERY" in upper:return 1,code
    if "DETAIL" in upper or result.metrics.details_failed:return 4,code
    return 2,code

def _finalize_stages(result,adapter_name,reporter,cli_started=None) -> None:
    """STEP92/96: stage [5/5] save + ✓ close + Chinese summary (no legacy text)."""
    reporter.stage(5)
    # Older tests stub generate_and_render_reports with the 3-arg signature.
    if "reporter" in inspect.signature(generate_and_render_reports).parameters:
        generate_and_render_reports(result,None,adapter_name,reporter=reporter)
    else:
        generate_and_render_reports(result,None,adapter_name)
    out_dir=getattr(reporter.artifacts,"output_directory",None)
    saved=len([p for p in Path(out_dir).iterdir() if p.is_file()]) if out_dir and Path(out_dir).exists() else 0
    if saved:reporter.stage_summary(5,f"已保存 {saved} 个文件")
    reporter.close_current()  # spec J: [5/5] ✓ before 完成 ✓ (also on FAILED)
    if result.status=="FAILED":
        stage,code=_failure_stage(result)
        reporter.failed_summary(stage,code)
    else:
        out_dir=getattr(reporter.artifacts,"output_directory",None)
        files=sorted(p.name for p in Path(out_dir).iterdir() if p.is_file()) if out_dir and Path(out_dir).exists() else []
        reporter.success_summary(result,reporter.artifacts,files)

def _run_stages(url,collect_fn,adapter_name,reporter,discovery_done=False,scope_count=None,cli_started=None) -> str|None:
    """STEP92: observation-only stages [1/5]-[4/5] around a collection call.

    The collector internals are untouched; stage facts are derived from the
    finished CollectionResult (jd_strategy / detail metrics / errors).
    """
    if discovery_done:
        # Stage 1 was started/closed by the caller around discovery; still
        # surface the recognized-source fact on the board suffix and in the
        # non-TTY log (STEP96).
        discovery_note=(f"{scope_count} 个招聘范围" if scope_count and scope_count > 1 else "已识别岗位数据源")
        reporter.stage_summary(1,discovery_note)
    else:
        reporter.stage(1)  # 识别招聘网站：adapter detect / discovery resolved here
        reporter.note(1,"已识别岗位数据源")
    reporter.stage(2)
    collect_started=perf_counter()
    try:outcome=collect_fn()
    except (ManualCurlResponseError, httpx.HTTPError) as exc:
        reporter.stage_fail(2,f"{type(exc).__name__}: {exc}")
        reporter.failed_summary(2,f"{type(exc).__name__}: {exc}")
        raise
    if outcome is None:return None  # plan-contract rejection already rendered by the caller
    collector,result=outcome
    if result is None:return None  # plan rejected: caller already rendered the reason
    # STEP96: total vs collection time are distinct numbers — the CLI total
    # includes discovery/navigation; the report keeps both explicitly.
    result.metrics.collection_elapsed_seconds=perf_counter()-collect_started
    if cli_started is not None:
        result.metrics.total_elapsed_seconds=perf_counter()-cli_started
    reporter.set_company(result.company)  # spec A: 公司 line once company is known
    unique=result.total_unique or len(result.jobs)
    raw_rows=getattr(result.metrics,"raw_rows",0) or 0
    reporter.note(2,f"{raw_rows} 条原始记录")
    if result.status=="FAILED":  # mark the actual failing stage; skip 3/4 detail stages
        stage,code=_failure_stage(result)
        reporter.stage_fail(stage,code)
        reporter.failed_summary(stage,code)
        return None
    reporter.stage(3)  # Collector has completed; this stage presents its final reconciliation.
    dupes=getattr(result.metrics,"duplicate_jobs",0) or 0
    cross_scope_merged=getattr(result.metrics,"cross_scope_merged_rows",0) or 0
    reporter.note(3,f"{unique} 个唯一岗位\n单范围重复：{dupes} · 跨范围合并：{cross_scope_merged}")
    reporter.stage(4)
    metrics=result.metrics
    if result.status!="FAILED":
        if metrics.jd_strategy=="LIST_SUFFICIENT" or (metrics.details_attempted==0 and metrics.details_succeeded==0):
            reporter.list_sufficient(result.total_unique,max(metrics.detail_request_seconds,metrics.detail_fallback_seconds))
        else:
            attempted=metrics.details_succeeded+metrics.details_failed
            reporter.detail_progress(attempted,metrics.details_required or attempted,ok=metrics.details_succeeded,fail=metrics.details_failed,elapsed=metrics.detail_request_seconds+metrics.detail_fallback_seconds)
    return _finalize_stages(result,adapter_name,reporter,cli_started)

def _collect_known_with_progress(url, adapter, reporter):
    """Optional observation hook; collection decisions remain in the adapter."""
    if isinstance(adapter,type) and issubclass(adapter,MokaAdapter):
        collector=adapter(progress_callback=reporter.observe)
        return collector,finalize_result(collector.collect(url),collector)
    return collect_url(url,adapter)

def _stdin_is_interactive() -> bool:
    """Keep automated runs/CI from ever waiting for fallback input."""
    return bool(getattr(sys.stdin, "isatty", lambda: False)())

def _read_curl_block(label: str) -> str | None:
    """Read pasted text only; it is parsed later and never sent to a shell."""
    typer.echo(f"请粘贴 {label}，完成后输入 END：")
    lines=[]
    stream=typer.get_text_stream("stdin")
    while True:
        line=stream.readline()
        if line == "": return None
        value=line.rstrip("\r\n")
        if value.strip().upper()=="CANCEL": return None
        if value.strip().upper()=="END": return "\n".join(lines).strip()
        if value.strip().upper().endswith("END"):
            typer.echo("请输入单独一行 END 结束粘贴。")
            continue
        lines.append(value)

def _fallback_audit(url: str, code: str, stage: str, **extra) -> dict:
    return {"original_url":url,"auto_failure_stage":stage,"auto_failure_code":code,
            "auto_failure_reason":code,"fallback_attempted":True,"fallback_method":"curl",**extra}

def _detail_page_factory(reporter):
    """Browser page factory for rendered detail pages (STEP94 RENDERED_PAGE).
    Returns None outside interactive runs — tests and CI never open a browser."""
    if not _stdin_is_interactive():
        return None
    def factory():
        from job_extractor.browser.runtime import BrowserRuntime
        launch_started=perf_counter()
        runtime=BrowserRuntime(); runtime.__enter__()
        page=runtime.page
        browser_debug={"browser_executable":"playwright-chromium","headless":True,
                       "console_error_count":0,"failed_request_count":0,"failed_requests":[]}
        if os.getenv("STEP94H_DEBUG")=="1":
            def console(message):
                if message.type == "error": browser_debug["console_error_count"] += 1
            def request_failed(request):
                parsed=urlsplit(request.url)
                browser_debug["failed_request_count"] += 1
                browser_debug["failed_requests"].append({"host":parsed.netloc,"path":parsed.path,
                                                          "resource_type":request.resource_type,
                                                          "reason":(request.failure or "")[:160]})
            page.on("console",console); page.on("requestfailed",request_failed)
        class _PageProxy:
            @property
            def step94h_browser(self):
                return {"factory":"_detail_page_factory","headless":True,
                        "launch_elapsed":perf_counter()-launch_started,**browser_debug}
            def __getattr__(self, name): return getattr(page, name)
            def close(self):
                try: runtime.close()
                except BaseException: pass
        return _PageProxy()
    return factory

def _detail_curl_menu(url: str, reporter: ProgressReporter, list_curl: str,
                      exc: DetailCurlRequired, observe):
    """After auto methods fail: Detail cURL (default), List-only, or cancel.
    List-only is a legal completion — files are written, status stays COMPLETE."""
    resolution=exc.resolution
    typer.echo("")
    typer.echo(f"未能自动获取完整 JD（{resolution.jd_failed} 个岗位）。")
    answer=typer.prompt("1. 提供 Detail / JD cURL\n2. 仅保存当前岗位 List\n3. 取消\n选择 [1]", default="1", show_default=False).strip().lower()
    if answer=="3":
        typer.echo("已取消。")
        reporter.stage_fail(4,"DETAIL_CANCELLED"); reporter.failed_summary(4,"DETAIL_CANCELLED")
        _write_fallback_failure(url,"DETAIL_CANCELLED","DETAIL",fallback_attempted=True,
                                curl_failure_stage="DETAIL",curl_failure_code="CANCEL",curl_failure_reason="CANCEL")
        return None
    if answer=="2":
        from job_extractor.manual_curl import ManualCurlResult
        ctx=exc.context
        result=ManualCurlResult(ctx.get("path",""),ctx.get("total"),ctx.get("score",0),"HIGH",
            exc.jobs,0,ctx.get("list_fetched",0),ctx.get("unique",0),[],
            "COMPLETE" if ctx.get("total") is not None and ctx.get("list_fetched",0)>=ctx.get("total") else "PARTIAL",
            ctx.get("termination",""),ctx.get("elapsed",0.0),ctx.get("pages",0),"LIST_ONLY")
        result.detail_resolution=resolution
        resolution.list_only_used=True
        return result
    # default: user Detail cURL — resume detail extraction on the saved list
    detail_curl=_read_curl_block("Detail / JD cURL")
    if detail_curl is None:
        typer.echo("已取消 cURL 兜底。")
        _write_fallback_failure(url,"DETAIL_CANCELLED","DETAIL",fallback_attempted=True,
                                curl_failure_stage="DETAIL",curl_failure_code="CANCEL",curl_failure_reason="CANCEL")
        return None
    try: parse_curl(detail_curl)
    except Exception as parse_error:
        typer.echo(f"cURL 解析失败\n原因：{parse_error}")
        _write_fallback_failure(url,"DETAIL_CURL_PARSE_FAILED","DETAIL",fallback_attempted=True,
                                curl_failure_stage="DETAIL",curl_failure_code="PARSE_FAILED",curl_failure_reason="DETAIL_CURL_PARSE_FAILED")
        return None
    from job_extractor.manual_curl import ManualCurlResult
    ctx=exc.context
    partial=ManualCurlResult(ctx.get("path",""),ctx.get("total"),ctx.get("score",0),"HIGH",
        exc.jobs,0,ctx.get("list_fetched",0),ctx.get("unique",0),[],
        "COMPLETE" if ctx.get("total") is not None and ctx.get("list_fetched",0)>=ctx.get("total") else "PARTIAL",
        ctx.get("termination",""),ctx.get("elapsed",0.0),ctx.get("pages",0),"LIST_ONLY")
    partial.detail_resolution=resolution
    return run_manual_curl(list_curl,detail_curl,max_jobs=None,progress_callback=observe)

def _write_fallback_failure(url: str, code: str, stage: str, **extra) -> None:
    audit=_fallback_audit(url,code,stage,**extra)
    failed=CollectionResult(source_url=url,status="FAILED",errors=[code],enrichment={"fallback":audit})
    generate_and_render_reports(failed,None,"GenericAdapter")

def _run_har_fallback(url: str, reporter: ProgressReporter, har_path: str,
                      auto_code: str, auto_stage: str) -> bool:
    """N6.2: browser-assisted fallback — ingest a user-exported HAR through
    the formal importer and push the resulting CollectionResult through the
    standard stage pipeline. Pure offline parsing; the path may be shown,
    HAR contents never dumped."""
    path=Path(har_path).expanduser()
    if not path.is_file():
        typer.echo(f"HAR 文件不存在：{path.name}")
        _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,
                                curl_failure_code="HAR_FILE_NOT_FOUND",
                                curl_failure_reason="HAR_FILE_NOT_FOUND")
        return False
    try:
        outcome=import_har(path)
    except Exception:
        typer.echo("HAR 导入失败：文件无法解析。")
        _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,
                                curl_failure_code="HAR_IMPORT_FAILED",
                                curl_failure_reason="HAR_IMPORT_FAILED")
        return False
    unified=outcome.result
    if not unified.jobs:
        typer.echo("HAR 中未找到可用的岗位数据。")
        _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,
                                curl_failure_code="HAR_NO_JOBS",
                                curl_failure_reason="HAR_NO_USABLE_JOB_RESPONSE")
        return False
    unified.enrichment["fallback"]=_fallback_audit(url,auto_code,auto_stage,
        fallback_method="har_import",har_file=path.name)
    reporter.use_curl_fallback(); reporter.stage(2)
    metrics=outcome.metrics
    typer.echo(f"HAR 导入成功\\n已恢复：{metrics['raw_rows']} raw / "
               f"{metrics['unique_jobs']} unique")
    reporter.note(2,f"{metrics['raw_rows']} 条原始记录")
    reporter.stage(3)
    reporter.note(3,f"唯一岗位 {metrics['unique_jobs']}")
    reporter.close_current(); reporter.stage(4)
    _finalize_stages(unified,"HarImportCollector",reporter)
    return True

def _pagination_helper_source() -> str | None:
    """The formal pagination helper shipped with this repository."""
    helper=Path(__file__).resolve().parents[1]/"scripts"/"pdd_browser_console_helper.js"
    if not helper.is_file():return None
    try:return helper.read_text(encoding="utf-8",errors="replace")
    except OSError:return None

def _copy_to_clipboard(text: str) -> bool:
    import shutil as _shutil, subprocess as _subprocess
    for command in (["pbcopy"],["clip"],["xsel","-b"],["xclip","-selection","clipboard"]):
        if not _shutil.which(command[0]):continue
        try:
            _subprocess.run(command,input=text.encode("utf-8"),check=True,timeout=5)
            return True
        except Exception:continue
    return False

def _open_in_browser(url: str) -> bool:
    import shutil as _shutil, subprocess as _subprocess
    for command in (["open",url],["xdg-open",url]):
        if not _shutil.which(command[0]):continue
        try:
            _subprocess.Popen(command,stdout=_subprocess.DEVNULL,stderr=_subprocess.DEVNULL)
            return True
        except Exception:continue
    return False

def _pick_har_path() -> str | None:
    """Native file picker first (macOS); drag-into-terminal path fallback.

    Returns a validated .har path, or None when the user cancels / the
    selection is not usable (the caller returns to the assist menu)."""
    import shutil as _shutil, subprocess as _subprocess
    for _attempt in range(3):
        picked=None
        if sys.platform=="darwin" and _shutil.which("osascript"):
            try:
                proc=_subprocess.run(
                    ["osascript","-e",
                     'POSIX path of (choose file with prompt "选择浏览器导出的 HAR 文件")'],
                    capture_output=True,text=True,timeout=180)
                if proc.returncode==0 and proc.stdout.strip():
                    picked=proc.stdout.strip()
                else:
                    return None  # user cancelled the native picker
            except Exception:
                picked=None
        if picked is None:
            typer.echo("请将 HAR 文件拖到此终端窗口，然后按回车")
            raw=typer.get_text_stream("stdin").readline()
            if raw=="":
                return None
            picked=raw.strip()
            if not picked:
                return None
        candidate=picked.strip()
        if (candidate.startswith("'") and candidate.endswith("'")) or (
                candidate.startswith('"') and candidate.endswith('"')):
            candidate=candidate[1:-1]
        candidate=candidate.replace("\\ "," ").strip()
        candidate=os.path.expanduser(candidate)
        if not candidate.lower().endswith(".har"):
            typer.echo("请选择 .har 浏览器采集文件。")
            continue
        return candidate
    return None

_BROWSER_ASSIST_STEPS=(
    "1. 打开职位列表页面",
    "2. 打开浏览器 Network 并开始记录",
    "3. 确保第一页请求已被记录",
    "4. 将每页数量调到最大",
    "5. 运行自动翻页助手",
    "6. 如出现网站验证，请手动完成",
    "7. 翻页完成后导出 HAR",
)

def _browser_assist_menu(url: str, reporter: ProgressReporter, *,
                         auto_code: str, auto_stage: str,
                         jobs_count: int | None = None,
                         curl_failure_code: str = "CURL_PAGINATION_NOT_REPLAYABLE") -> bool:
    """Browser-assisted collection UX (or a clean exit).

    jobs_count set = proven "current page valid, pagination rejected"
    (recommended path). jobs_count None = user-initiated upgrade from a
    MANUAL_CURL_INVALID outcome (user choice, not a system reclassification)."""
    typer.echo("")
    typer.echo("浏览器辅助采集")
    if jobs_count is not None:
        typer.echo(f"当前 cURL 可以读取岗位（{jobs_count} 条），但该站点的分页请求"
                   "使用动态验证信息，无法安全使用同一请求继续翻页。")
    typer.echo("该网站需要在正常浏览器中完成分页请求。接下来：")
    for step in _BROWSER_ASSIST_STEPS:
        typer.echo(step)
    answer=typer.prompt("[1] 开始浏览器辅助\n[2] 我已经有 HAR\n[3] 退出\n选择 [1]",
                        default="1", show_default=False).strip().lower()
    if answer=="3":
        typer.echo("已结束。")
        _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,
                                curl_failure_stage="LIST",
                                curl_failure_code=curl_failure_code,
                                curl_failure_reason=curl_failure_code)
        return False
    if answer=="2":
        har_path=_pick_har_path()
        if har_path is None:
            return _browser_assist_menu(url,reporter,auto_code=auto_code,
                                        auto_stage=auto_stage,jobs_count=jobs_count,
                                        curl_failure_code=curl_failure_code)
        reporter.use_curl_fallback()
        return _run_har_fallback(url,reporter,har_path,auto_code,auto_stage)
    # [1] 开始浏览器辅助：最小自动化 = 打开正常招聘页 + 复制助手到剪贴板。
    # 不注入 Console JS、不碰验证弹窗、不读 Cookie —— 粘贴与验证由用户完成。
    helper=_pagination_helper_source()
    copied=bool(helper and _copy_to_clipboard(helper))
    if url and _open_in_browser(url):
        typer.echo("✓ 已打开职位列表页面")
    if copied:
        typer.echo("✓ 自动翻页助手已复制到剪贴板")
    elif helper is not None:
        typer.echo("自动翻页助手复制失败。助手文件位于仓库 scripts/pdd_browser_console_helper.js，"
                   "可手动打开复制。")
    else:
        typer.echo("自动翻页助手不可用，请继续手动翻页。")
    typer.echo("请在浏览器中：")
    for step in ("1. 打开开发者工具 → Network",
                 "2. 开始录制并刷新第一页",
                 "3. 打开 Console",
                 "4. 粘贴刚刚复制的代码并执行",
                 "5. 等待自动翻页完成",
                 "6. 如出现验证，请手动完成",
                 "7. 导出 HAR"):
        typer.echo(step)
    while True:
        answer=typer.prompt("[1] 选择 HAR 文件\n[2] 重新复制助手\n[3] 退出\n选择 [1]",
                            default="1", show_default=False).strip().lower()
        if answer=="3":
            typer.echo("已结束。")
            _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,
                                    curl_failure_stage="LIST",
                                    curl_failure_code=curl_failure_code,
                                    curl_failure_reason=curl_failure_code)
            return False
        if answer=="2":
            helper=_pagination_helper_source()
            if helper and _copy_to_clipboard(helper):
                typer.echo("✓ 自动翻页助手已复制到剪贴板")
            else:
                typer.echo("自动翻页助手复制失败，请手动打开 scripts/pdd_browser_console_helper.js 复制。")
            continue
        har_path=_pick_har_path()
        if har_path is None:
            continue
        reporter.use_curl_fallback()
        return _run_har_fallback(url,reporter,har_path,auto_code,auto_stage)

def _interactive_curl_fallback(url: str, reporter: ProgressReporter, *, auto_code: str, auto_stage: str="DISCOVERY") -> bool:
    """Bridge an auto failure to the existing safe Manual cURL collector."""
    if not _stdin_is_interactive():
        _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=False)
        return False
    typer.echo("")
    answer=typer.prompt("是否使用 cURL 兜底？ [Y/n]", default="Y", show_default=False).strip().lower()
    if answer not in {"", "y", "yes"}:
        typer.echo("已结束。")
        _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=False)
        return False
    list_curl=None
    for _attempt in range(2):
        text=_read_curl_block("List cURL")
        if text is None:
            typer.echo("已取消 cURL 兜底。")
            _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage="LIST",curl_failure_code="CANCEL",curl_failure_reason="CANCEL")
            return False
        try: parse_curl(text); list_curl=text; break
        except Exception as exc: typer.echo(f"cURL 解析失败\n原因：{exc}")
    if list_curl is None:
        typer.echo("cURL 兜底失败")
        _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage="LIST",curl_failure_code="PARSE_FAILED",curl_failure_reason="LIST_CURL_PARSE_FAILED")
        return False
    reporter.use_curl_fallback(); reporter.stage(2); detail_started=False
    def observe(event: str, **data) -> None:
        nonlocal detail_started
        if event=="list":
            reporter.page_progress(data["page"],data.get("pages")); reporter.job_progress(data["fetched"],data.get("total") or 0)
        elif event=="list_complete" and reporter.current==2:
            reporter.note(2,f"获取 {data['fetched']} 个岗位"); reporter.stage(3)
        elif event=="list_ready" and reporter.current==3:
            reporter.note(3,f"唯一岗位 {data['unique']}"); reporter.close_current(); reporter.stage(4); detail_started=True
        elif event=="detail":
            if not detail_started:
                reporter.note(2,f"获取 {data['total']} 个岗位"); reporter.stage(3); reporter.close_current(); reporter.stage(4); detail_started=True
            reporter.detail_progress(data["done"],data["total"],data.get("ok",0),data.get("fail",0),data.get("title"))
            if data.get("failure_code"):
                reporter.error(job_id=data.get("post_id"),title=data.get("title"),stage="DETAIL",
                               message=data.get("failure_reason") or data["failure_code"])
        elif event=="detail_debug" and os.getenv("STEP94H_DEBUG")=="1":
            detail=data.get("detail") or {}
            safe={key:detail.get(key) for key in ("campaign_source_url","campaign_context","browser_factory","browser_launch_elapsed","resolver_state_id","pattern_locked","candidate_url","navigation_wait","navigation_elapsed","final_page_url","body_text_length","ready_state","jd_heading_found","responsibilities_found","requirements_found","failure_stage","failure_code","failure_reason") if key in detail}
            typer.echo(f"DETAIL DEBUG #{data.get('post_id')} {data.get('title')}: {safe}")
    manual=None
    while True:
        try:
            manual=run_manual_curl(list_curl,None,max_jobs=None,progress_callback=observe,
                                   detail_browser_factory=_detail_page_factory(reporter),
                                   detail_page_url=url,campaign_context=url)
            break
        except PageReplayRejected as exc:
            stage=4 if detail_started else 2
            reporter.stage_fail(stage,exc.code); reporter.failed_summary(stage,exc.code)
            typer.echo("cURL 已读取当前页，但无法继续安全分页")
            recovered=_browser_assist_menu(url,reporter,auto_code=auto_code,
                                         auto_stage=auto_stage,
                                         jobs_count=exc.jobs_count)
            return recovered
        except httpx.HTTPError as exc:
            # N8.3: a transport-level rejection of the PASTED cURL itself
            # (HTTPStatusError/timeout/connection) means the cURL could not be
            # validated by replay — classified as MANUAL_CURL_INVALID. The error
            # code semantics never change, but the CLI offers a user-initiated
            # upgrade path: retry the cURL, switch to browser-assisted
            # collection, or exit. Only the proven "current page valid,
            # pagination rejected" outcome (PageReplayRejected, requiring
            # jobs>=1 from the initial request) treats Browser Assist as the
            # recommended path; here it is purely user-initiated.
            status=getattr(getattr(exc,"response",None),"status_code",None)
            code=(f"HTTP_{status}" if isinstance(status,int)
                  else "TIMEOUT" if isinstance(exc,httpx.TimeoutException)
                  else "CONNECTION_ERROR")
            stage=4 if detail_started else 2
            reporter.stage_fail(stage,code); reporter.failed_summary(stage,code)
            typer.echo("无法从这条 cURL 识别有效岗位数据。")
            if detail_started or not _stdin_is_interactive():
                _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,
                                        curl_failure_stage="LIST",
                                        curl_failure_code="MANUAL_CURL_INVALID",
                                        curl_failure_reason=code)
                return False
            typer.echo("该请求无法在 Job Extractor 中重放。"
                       "你可以重新复制 cURL，或改用浏览器辅助采集。")
            answer=typer.prompt("[1] 重新粘贴 cURL\n[2] 使用浏览器辅助采集\n[3] 退出\n选择 [1]",
                                default="1", show_default=False).strip().lower()
            if answer=="2":
                return _browser_assist_menu(url,reporter,auto_code=auto_code,
                                          auto_stage=auto_stage, jobs_count=None,
                                          curl_failure_code="MANUAL_CURL_INVALID")
            if answer=="3":
                typer.echo("已结束。")
                _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,
                                        curl_failure_stage="LIST",
                                        curl_failure_code="MANUAL_CURL_INVALID",
                                        curl_failure_reason=code)
                return False
            text=_read_curl_block("List cURL")
            if text is None:
                typer.echo("已取消 cURL 兜底。")
                _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage="LIST",curl_failure_code="CANCEL",curl_failure_reason="CANCEL")
                return False
            try:
                parse_curl(text); list_curl=text
            except Exception as parse_error:
                typer.echo(f"cURL 解析失败\n原因：{parse_error}")
                _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage="LIST",curl_failure_code="PARSE_FAILED",curl_failure_reason="LIST_CURL_PARSE_FAILED")
                return False
            continue
        except ManualCurlResponseError as exc:
            stage=4 if detail_started else 2; reporter.stage_fail(stage,exc.code); reporter.failed_summary(stage,exc.code)
            _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage=stage,curl_failure_code=exc.code,curl_failure_reason=translate_reason(exc.code))
            return False
        except DetailCurlRequired as exc:
            manual=_detail_curl_menu(url,reporter,list_curl,exc,observe)
            if manual is None: return False
            break
        except ValueError as exc:
            if str(exc)!="DETAIL_CURL_REQUIRED": raise
            detail_curl=_read_curl_block("Detail / JD cURL")
            if detail_curl is None:
                typer.echo("已取消 cURL 兜底。")
                _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage="DETAIL",curl_failure_code="CANCEL",curl_failure_reason="CANCEL")
                return False
            try: parse_curl(detail_curl)
            except Exception as parse_error:
                typer.echo(f"cURL 解析失败\n原因：{parse_error}")
                _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage="DETAIL",curl_failure_code="PARSE_FAILED",curl_failure_reason="DETAIL_CURL_PARSE_FAILED")
                return False
            manual=run_manual_curl(list_curl,detail_curl,max_jobs=None,progress_callback=observe)
            break
        except Exception as exc:
            stage=4 if detail_started else 2; reporter.stage_fail(stage,str(exc)); reporter.failed_summary(stage,str(exc))
            _write_fallback_failure(url,auto_code,auto_stage,fallback_attempted=True,curl_failure_stage="DETAIL" if detail_started else "LIST",curl_failure_code=type(exc).__name__,curl_failure_reason=str(exc))
            return False
    unified=manual_result_to_collection_result(manual,url); unified.enrichment["fallback"]=_fallback_audit(url,auto_code,auto_stage)
    reporter.set_company(unified.company)
    if unified.metrics.jd_strategy=="LIST_SUFFICIENT":
        reporter.list_sufficient(unified.total_unique)
    elif not detail_started:
        reporter.note(2,f"获取 {unified.total_unique} 个岗位"); reporter.stage(3); reporter.note(3,f"唯一岗位 {unified.total_unique}"); reporter.close_current(); reporter.stage(4)
    if unified.metrics.jd_strategy=="LIST_SUFFICIENT":
        typer.echo("List 已包含完整 JD，跳过 Detail cURL。")
    _finalize_stages(unified,"ManualCurlCollector",reporter)
    return True

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
    list_curl: str|None = typer.Option(None,"--list-curl",help="Explicit public list cURL; parsed but never shell-executed."),
    detail_curl: str|None = typer.Option(None,"--detail-curl",help="Explicit public detail cURL; parsed but never shell-executed."),
    list_curl_file: Path|None = typer.Option(None,"--list-curl-file",help="UTF-8 file containing an explicit public list cURL."),
    detail_curl_file: Path|None = typer.Option(None,"--detail-curl-file",help="UTF-8 file containing an explicit public detail cURL."),
    scope: str|None = typer.Option(None,"--scope",help="Resolve an ambiguous recruitment scope: campus, social, intern, or all."),
) -> None:
    initialize_step94h_debug()
    if list_curl_file:
        if list_curl:raise typer.BadParameter("Use either --list-curl or --list-curl-file, not both.")
        list_curl=list_curl_file.read_text(encoding="utf-8")
    if detail_curl_file:
        if detail_curl:raise typer.BadParameter("Use either --detail-curl or --detail-curl-file, not both.")
        detail_curl=detail_curl_file.read_text(encoding="utf-8")
    if list_curl or detail_curl:
        if not list_curl or not detail_curl:raise typer.BadParameter("Both --list-curl and --detail-curl are required.")
        try:result=run_manual_curl(list_curl,detail_curl,max_jobs=None)
        except Exception as exc:typer.echo(f"Manual cURL fallback failed: {type(exc).__name__}: {exc}");return
        source=(url or result.jobs[0].source_url) if result.jobs else (url or "manual-curl")
        unified=manual_result_to_collection_result(result,source)
        ensure_directories()
        reporter=ProgressReporter();reporter.begin(source)  # manual cURL pre-collects: stages 1-4 already done
        reporter.stage(1);reporter.stage(2);reporter.stage(3);reporter.stage(4)
        _finalize_stages(unified,"ManualCurlCollector",reporter)
        return
    if url is None:
        raise typer.BadParameter("Missing recruitment page URL.", param_hint="URL")
    cli_started=perf_counter()  # STEP96: wall-clock total for the whole run
    if scope and scope.lower() not in {"campus","social","intern","all"}:
        raise typer.BadParameter("Scope must be campus, social, intern, or all.", param_hint="--scope")
    if scope and scope.lower()=="all":scope="ALL_JOBS"
    elif scope:scope=scope.upper()
    ensure_directories()
    adapter = default_registry.detect(url)
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
                reporter=ProgressReporter();reporter.begin(handoff.url)
                text=_run_stages(handoff.url,lambda:collect_url(handoff.url,default_registry.detect_known(handoff.url)),handoff.adapter_name,reporter,cli_started=cli_started)
                if text:typer.echo(text)
                return
            if navigation.graph.terminal_reason=="SCOPE_SELECTION_REQUIRED":
                typer.echo(f"Scope selection required: {', '.join(navigation.graph.available_scopes)}");return
            if not validation.valid:
                typer.echo("Plan requires review. Generic collection stopped.");return
            reporter=ProgressReporter();reporter.begin(url)
            text=_run_stages(url,lambda:(generic,execute_plan_or_report(generic,collection_plan)),generic.__class__.__name__,reporter,cli_started=cli_started)
            if text:typer.echo(text)
            return
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
        reporter=ProgressReporter();reporter.begin(url);reporter.stage(1);generic=adapter()
        try:
            result=generic.discover(url)
        except Exception as exc:  # STEP76A2: discovery always renders a terminal result — never a silent exit.
            reporter.stage_fail(1,"DISCOVERY_FAILED");reporter.discovery_failure("DISCOVERY_FAILED")
            _interactive_curl_fallback(url,reporter,auto_code="DISCOVERY_FAILED");return
        filename=build_output_filename(url,datetime.now()).removesuffix(".json")+"_discovery.json"
        try:
            runtime_resolution=resolve_encrypted_runtime_terminal(result,generic.discover_terminal,default_registry) if result.probable_list_api is None else None
        except Exception as exc:  # STEP76A2: terminal probing failure stays a rendered terminal outcome.
            typer.echo(f"Terminal resolution failure: {type(exc).__name__}: {exc}");runtime_resolution=None
        if runtime_resolution:
            entry,terminal,fingerprint=runtime_resolution
            typer.echo(f"Encrypted runtime terminal: {fingerprint.provider} ({', '.join(fingerprint.capabilities)}) -> {entry.url}")
            runtime_plan=generic.build_plan(terminal);runtime_validation=CollectionPlanValidator().validate(runtime_plan)
            if runtime_plan.mode=="BROWSER_RUNTIME_DATA" and runtime_validation.valid:
                source=runtime_plan.runtime_source or {}
                typer.echo(f"Runtime pagination: model={source.get('pagination_model')} total={source.get('total')} limit={source.get('limit')} pages={source.get('page_count')}")
                collected=execute_plan_or_report(generic,runtime_plan)
                if collected is None:return
                text=_run_stages(url,lambda:(generic,collected),generic.__class__.__name__,reporter,discovery_done=True,cli_started=cli_started)
                if text:typer.echo(text)
                return
        reporter.discovery_scope_progress()
        try:
            navigation,stability=stable_navigation(url,generic.discover,default_registry,scope,initial_result=result,deadline_seconds=75)
        except Exception as exc:  # STEP76A2: navigation must surface a terminal result — never a silent exit.
            reporter.stage_fail(1,"DISCOVERY_FAILED");reporter.discovery_failure("DISCOVERY_FAILED")
            _interactive_curl_fallback(url,reporter,auto_code="DISCOVERY_FAILED");return
        handoff=navigation.handoff;result=navigation.terminal_result
        output_path=DISCOVERY_OUTPUT_DIR/filename;output_path.write_text(result.model_dump_json(indent=2),encoding="utf-8")
        if handoff:
            text=_run_stages(handoff.url,lambda:collect_url(handoff.url,default_registry.detect_known(handoff.url)),handoff.adapter_name,reporter,discovery_done=True,cli_started=cli_started)
            if text:typer.echo(text)
            return
        if navigation.graph.terminal_reason=="SCOPE_SELECTION_REQUIRED":
            reporter.stage_fail(1,"DISCOVERY_FAILED");reporter.discovery_failure("DISCOVERY_FAILED")
            _interactive_curl_fallback(url,reporter,auto_code="DISCOVERY_FAILED");return
        collection_plan=generic.build_plan(result);validation=CollectionPlanValidator().validate(collection_plan)
        if can_auto_execute_generic(collection_plan,validation,navigation,result):
            scope_count=len(collection_plan.list_scopes) or None
            text=_run_stages(url,lambda:(generic,execute_plan_or_report(generic,collection_plan)),generic.__class__.__name__,reporter,discovery_done=True,scope_count=scope_count,cli_started=cli_started)
            if text:typer.echo(text)
            return
        reason="TIMEOUT" if result.status=="TIMEOUT" else "UNSUPPORTED" if collection_plan.mode=="UNSUPPORTED" else "DISCOVERY_FAILED"
        reporter.stage_fail(1,reason);reporter.discovery_failure(reason)
        _interactive_curl_fallback(url,reporter,auto_code=reason)
        return

    reporter=ProgressReporter();reporter.begin(url)
    text=_run_stages(url,lambda:_collect_known_with_progress(url,adapter,reporter),adapter.__name__,reporter,cli_started=cli_started)
    if text:typer.echo(text)

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
    # No deeper recruitment terminal means the current page itself is the
    # verified source.  It is not contradictory evidence and must not turn a
    # valid, high-confidence list API into a Stage 1 discovery failure.
    terminal_allows_execution=graph.terminal_reason in ("HIGH_CONFIDENCE_GENERIC_PLAN","NO_RECRUITMENT_TERMINAL")
    scope_allows_execution=(graph.terminal_reason=="NO_RECRUITMENT_TERMINAL"
                            or graph.selected_scope in (graph.source_intent,"ALL_JOBS"))
    return (result.status=="DISCOVERED" and validation.valid and plan.executable and plan.confidence=="HIGH"
        and graph.stability_state!="UNSTABLE" and terminal_allows_execution and scope_allows_execution
        and not graph.available_scopes and not plan.total_conflict and not blocked_warning
        and not any("SENSITIVE" in error.upper() for error in validation.errors) and positive_evidence)
