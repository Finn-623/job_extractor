"""N7B-F3.3: offline 574-job JD lifecycle acceptance.

The real Manual-cURL/CLI fallback path is exercised with an httpx mock and a
fake browser: no site traffic and no live browser are used.
"""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path

import httpx
import pytest

import job_extractor.cli as cli
import job_extractor.step94h_debug as trace
from job_extractor.manual_curl import (DetailCurlRequired, load_jd_checkpoint,
                                       manual_result_to_collection_result,
                                       resume_manual_checkpoint, run_manual_curl,
                                       _bounded_detail_futures)
from job_extractor.progress import ProgressReporter


LIST_URL = "https://f33.test/wecruit/positionInfo/listPosition/CAMP?iSaJAx=isAjax"
LIST_CURL = f"curl '{LIST_URL}' -H 'Accept: application/json'"
DETAIL_CURL = "curl 'https://f33.test/detail?id=demo'"
WORK = ("负责大型系统设计、交付与跨团队协作，持续改善工程质量并推动稳定性治理；"
        "负责需求分析、技术方案评审、实施落地和复杂问题闭环，保障长期业务交付质量。") * 3
REQ = ("本科及以上学历，具备扎实工程实践、系统设计能力与跨团队沟通能力；"
       "能够独立解决复杂技术问题，持续推动自动化建设、质量改进和工程化交付。") * 3


def _rows():
    return [{"postId": f"p{i}", "jobName": f"岗位{i}", "postType": "campus", "recruitType": "1"}
            for i in range(1, 575)]


def _detail(job_id: str) -> dict:
    return {"data": {"postId": job_id, "postName": job_id,
                      "workContent": WORK, "serviceCondition": REQ}}


def _patch_runtime(monkeypatch, tmp_path, requested):
    real_client = httpx.Client
    def handler(request):
        if request.url.path.endswith("/listPosition/CAMP"):
            return httpx.Response(200, json={"data": {"list": _rows(), "total": 574}, "success": True})
        if "listPositionDetail" in request.url.path:
            post_id = dict(piece.split("=", 1) for piece in request.content.decode().split("&"))["postId"]
            requested.append(post_id)
            # Permit several worker calls to overlap so this verifies measured
            # peak concurrency rather than the configured worker count.
            time.sleep(0.001)
            return httpx.Response(200 if int(post_id[1:]) <= 488 else 404,
                                  json=_detail(post_id) if int(post_id[1:]) <= 488 else {})
        if request.url.path == "/detail":
            requested.append("resume:" + request.url.params["id"])
            return httpx.Response(200, json={"data": {"jobDuty": WORK, "jobRequirements": REQ}})
        return httpx.Response(404)
    class Factory:
        def __call__(self, **kwargs):
            return real_client(transport=httpx.MockTransport(handler), timeout=30, follow_redirects=True)
    monkeypatch.setattr("job_extractor.manual_curl.httpx.Client", Factory())
    monkeypatch.setattr("job_extractor.config.OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "_detail_page_factory", lambda _reporter: lambda: object())
    # The production renderer is not under test here; its aggregate contract is.
    monkeypatch.setattr("job_extractor.collectors.detail_resolver.render_detail_jobs",
                        lambda _factory, targets, **_kwargs: (0, len(targets)))


def _exports(root: Path) -> Path:
    return next(path.parent for path in root.rglob("jobs.json") if "_checkpoints" not in path.parts)


def test_f33_interactive_save_partial_574_lifecycle(monkeypatch, tmp_path):
    requested = []
    _patch_runtime(monkeypatch, tmp_path, requested)
    debug = tmp_path / "events.log"
    monkeypatch.setenv("STEP94H_DEBUG", "1")
    monkeypatch.setattr(trace, "DEBUG_PATH", debug)
    trace.initialize()
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: True)
    answers = iter(("y", "2"))
    monkeypatch.setattr(cli.typer, "prompt", lambda *_a, **_k: next(answers))
    monkeypatch.setattr(cli, "_read_curl_block", lambda _label: LIST_CURL)
    reporter = ProgressReporter(out=lambda _s: None, write=lambda _s: None, tty=False)
    reporter.begin("https://f33.test/campaign")

    assert cli._interactive_curl_fallback("https://f33.test/campaign", reporter,
                                          auto_code="DISCOVERY_FAILED") is True
    checkpoint = max((tmp_path / "_checkpoints").glob("*/jd_checkpoint.json"), key=lambda p: p.stat().st_mtime)
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    out = _exports(tmp_path)
    exported = json.loads((out / "jobs.json").read_text(encoding="utf-8"))
    summary = json.loads((out / "collection.json").read_text(encoding="utf-8"))
    events = [json.loads(line)["phase"] for line in debug.read_text(encoding="utf-8").splitlines()[1:]]

    assert len(exported["jobs"]) == 574
    assert summary["status"] == "INCOMPLETE"
    # Peak is measured while the API worker calls overlap; partial-result
    # construction must not fall back to the model default of one.
    assert exported["metrics"]["max_concurrency_observed"] > 1
    assert payload["stage"] == "JD_PARTIAL"
    assert len(payload["task_states"]) == 574
    assert sum(value == "SUCCESS" for value in payload["task_states"].values()) == 488
    assert sum(value == "FAILED" for value in payload["task_states"].values()) == 86
    assert all(job["full_jd"] for job in exported["jobs"][:488])
    assert all(not job["full_jd"] for job in exported["jobs"][488:])
    assert {"jobs.json", "jobs.csv", "jobs.xlsx", "collection.json", "report.md"} <= {p.name for p in out.iterdir()}
    assert {"JD_API_COMPLETE", "JD_RENDER_FALLBACK_START", "JD_RENDER_FALLBACK_END",
            "JD_WAIT_DETAIL_CURL", "JD_SAVE_PARTIAL", "COLLECTION_RESULT_READY",
            "EXPORT_START", "EXPORT_END", "RESOURCE_CLEANUP_START", "RESOURCE_CLEANUP_END"} <= set(events)
    ordered = ("JD_API_COMPLETE", "JD_RENDER_FALLBACK_START", "JD_RENDER_FALLBACK_END",
               "JD_WAIT_DETAIL_CURL", "RESOURCE_CLEANUP_START", "RESOURCE_CLEANUP_END",
               "JD_SAVE_PARTIAL", "COLLECTION_RESULT_READY", "EXPORT_START", "EXPORT_END")
    assert [events.index(phase) for phase in ordered] == sorted(events.index(phase) for phase in ordered)
    assert reporter.current is None and reporter.states[4] == "done"
    assert len([item for item in requested if item.startswith("p")]) == 574


def test_f33_continue_uses_checkpoint_targets_only(monkeypatch, tmp_path):
    requested = []
    _patch_runtime(monkeypatch, tmp_path, requested)
    # Reuse a small old-style checkpoint: resume must target only missing JD,
    # not replay the original list endpoint or overwrite credible content.
    from job_extractor.checkpoints import CheckpointWriter
    from job_extractor.models import Job
    prior = "岗位职责\n" + WORK + "\n任职要求\n" + REQ
    old = [Job(job_id=str(i), job_title=str(i), source_url="https://f33.test", full_jd=(prior if i < 2 else None)) for i in range(4)]
    source = CheckpointWriter(tmp_path, "https://f33.test").save(old, stage="JD_PARTIAL", task_states={"2":"FAILED", "3":"PENDING"})
    result = resume_manual_checkpoint(source, DETAIL_CURL, checkpoint_root=tmp_path)
    assert [item for item in requested if item.startswith("resume:")] == ["resume:2", "resume:3"]
    assert all(job.full_jd for job in result.jobs)


def test_f33_old_empty_states_remain_loadable(tmp_path):
    payload = {"format":"job-extractor-jd-checkpoint-v1", "source_url":"https://f33.test", "stage":"JD_ABORTED",
               "jobs": [], "task_states": {}}
    path = tmp_path / "old.json"; path.write_text(json.dumps(payload), encoding="utf-8")
    jobs, states, _url = load_jd_checkpoint(path)
    assert jobs == [] and states == {}


def test_f34_non_tty_saves_partial_without_prompt_or_ansi(monkeypatch, tmp_path):
    requested = []
    _patch_runtime(monkeypatch, tmp_path, requested)
    monkeypatch.setattr(cli, "_stdin_is_interactive", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *_a, **_k: pytest.fail("non-TTY must not prompt"))
    with pytest.raises(DetailCurlRequired) as raised:
        run_manual_curl(LIST_CURL, None, max_jobs=None, detail_browser_factory=lambda: object())
    lines = []
    reporter = ProgressReporter(out=lines.append, write=lines.append, tty=False)
    reporter.begin("https://f33.test/campaign"); reporter.stage(4)
    partial = cli._detail_curl_menu("https://f33.test/campaign", reporter, LIST_CURL,
                                    raised.value, lambda *_a, **_k: None)
    unified = manual_result_to_collection_result(partial, "https://f33.test/campaign")
    cli._finalize_stages(unified, "ManualCurlCollector", reporter)
    checkpoint = max((tmp_path / "_checkpoints").glob("*/jd_checkpoint.json"), key=lambda p: p.stat().st_mtime)
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    out = _exports(tmp_path)
    text = "\n".join(lines)
    assert unified.status == "INCOMPLETE" and len(unified.jobs) == 574
    assert payload["stage"] == "JD_PARTIAL" and len(payload["task_states"]) == 574
    assert len(json.loads((out / "jobs.json").read_text(encoding="utf-8"))["jobs"]) == 574
    assert "\x1b[" not in text and text.count("完成 ✓") == 1
    # Pipe mode logs the opened and closed current stage, but never redraws a
    # five-stage board or restarts earlier stages.
    assert "[1/5]" not in text and "[2/5]" not in text and "[3/5]" not in text
    assert text.count("[4/5]") == 2 and text.count("[5/5]") == 2


def test_f34_keyboard_interrupt_aborts_with_state_snapshot_and_cleanup(monkeypatch, tmp_path):
    requested = []
    _patch_runtime(monkeypatch, tmp_path, requested)
    debug = tmp_path / "interrupt-events.log"
    monkeypatch.setenv("STEP94H_DEBUG", "1")
    monkeypatch.setattr(trace, "DEBUG_PATH", debug)
    trace.initialize()
    monkeypatch.setattr("job_extractor.collectors.detail_resolver.render_detail_jobs",
                        lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        run_manual_curl(LIST_CURL, None, max_jobs=None, detail_browser_factory=lambda: object())
    checkpoint = max((tmp_path / "_checkpoints").glob("*/jd_checkpoint.json"), key=lambda p: p.stat().st_mtime)
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    phases = [json.loads(line)["phase"] for line in debug.read_text(encoding="utf-8").splitlines()[1:]]
    assert payload["stage"] == "JD_ABORTED" and len(payload["jobs"]) == 574
    assert sum(v == "SUCCESS" for v in payload["task_states"].values()) == 488
    # The direct API has already conclusively failed these 86 before Ctrl+C
    # interrupts the renderer, so FAILED is the accurate preserved state.
    assert sum(v == "FAILED" for v in payload["task_states"].values()) == 86
    assert "RESOURCE_CLEANUP_START" in phases and "RESOURCE_CLEANUP_END" in phases
    assert not list(tmp_path.rglob("jobs.json"))


def test_n7c_budget_reports_started_futures_while_safely_draining():
    """Budget expiry stops submission; it never pretends running work ended."""
    release=threading.Event(); draining=threading.Event(); seen=[]
    class Budget:
        def __init__(self): self.calls=0
        def allow(self): self.calls+=1; return self.calls <= 2
        def remaining(self): return 0.0
    def work(item):
        release.wait(1.0)
        return item
    def run():
        _bounded_detail_futures([1,2,3],work,2,Budget(),state_callback=lambda **data:(seen.append(data),draining.set()))
    thread=threading.Thread(target=run)
    thread.start(); assert draining.wait(1.0)
    assert seen == [{"running":2}]
    release.set(); thread.join(1.0)
    assert not thread.is_alive()
