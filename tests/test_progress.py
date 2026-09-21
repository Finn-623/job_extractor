"""STEP92 tests: terminal progress UX (tests/test_progress.py)."""
from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from job_extractor.models import CollectionMetrics, CollectionResult, Job
from job_extractor.reporting.manager import ReportArtifacts
from job_extractor.progress import (
    ProgressReporter, STAGES, fmt_clock, progress_bar, translate_reason,
)


class Capture:
    """Injectable sink: out() -> permanent lines, write() -> raw TTY chunks."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.raw: list[str] = []

    def __call__(self, text: str) -> None:
        self.lines.append(text)

    def write(self, text: str) -> None:
        self.raw.append(text)

    @property
    def raw_text(self) -> str:
        return "".join(self.raw)


def make_reporter(tty: bool = False, interval: float = 0.2) -> tuple[ProgressReporter, Capture]:
    sink = Capture()
    t = {"now": 1000.0}

    def clock() -> float:
        return t["now"]

    rep = ProgressReporter(out=sink, write=sink.write, clock=clock, tty=tty, interval=interval)
    rep._t = t
    return rep, sink


def advance(rep: ProgressReporter, seconds: float) -> None:
    rep._t["now"] += seconds


def job(n: int = 1) -> Job:
    return Job(job_id=f"J{n}", job_title=f"岗 {n}", company="测试公司",
               source_url="https://x.example/a", detail_url="https://x.example/a/1",
               apply_url="https://x.example/a/1/apply",
               responsibilities=["职责"], requirements=["要求"])


def result(status: str = "COMPLETE", jobs: int = 2, *, strategy="LIST_SUFFICIENT",
           detail_ok: int = 0, detail_fail: int = 0) -> CollectionResult:
    metrics = CollectionMetrics(jd_strategy=strategy, details_succeeded=detail_ok,
                                details_failed=detail_fail, elapsed_seconds=8.4)
    return CollectionResult(platform="zhiye", company="鸿擎科技",
                            source_url="https://x.example/a", status=status,
                            jobs=[job(i) for i in range(1, jobs + 1)],
                            metrics=metrics)


def artifacts(tmp_path) -> ReportArtifacts:
    d = tmp_path / "run"
    d.mkdir(parents=True, exist_ok=True)
    for name in ("jobs.json", "jobs.xlsx", "report.md", "collection.json"):
        (d / name).write_text("x", encoding="utf-8")
    return ReportArtifacts(output_directory=d, json_path=d / "jobs.json",
                           excel_path=d / "jobs.xlsx", markdown_path=d / "report.md")


# 1 — stage start/complete checklist ---------------------------------------
def test_stage_start_and_complete_lines():
    rep, sink = make_reporter()
    rep.stage(1)
    assert "[1/5] 识别招聘网站" in sink.lines[-1]
    advance(rep, 1.0)
    rep.stage(2)  # opening stage 2 auto-completes stage 1
    assert any("识别招聘网站" in l and "✓" in l for l in sink.lines)


# 2 — per-stage timing -------------------------------------------------------
def test_stage_timing_recorded():
    rep, sink = make_reporter()
    rep.stage(2)
    advance(rep, 8.4)
    rep.stage(3)
    assert "8.4s" in sink.lines[-2]  # close line for stage 2 precedes stage 3 banner
    assert rep.stage_elapsed[1] == pytest.approx(8.4)


# 3 — pagination -------------------------------------------------------------
def test_page_progress_with_and_without_total():
    rep, sink = make_reporter()
    rep.page_progress(1, 40)
    assert sink.lines[-1] == "当前页：1/40"
    rep.page_progress(3)
    assert sink.lines[-1] == "当前页：3/?"


# 4 — jobs counter + current title ------------------------------------------
def test_job_progress_counter_and_title():
    rep, sink = make_reporter()
    rep.job_progress(178, 357, "高级车身集成工程师")
    assert "岗位：178 / 357" in sink.lines
    assert "当前：高级车身集成工程师" in sink.lines


# 5 — JD bar + percentage ----------------------------------------------------
def test_detail_progress_bar_and_percent():
    rep, sink = make_reporter()
    rep.detail_progress(178, 357, ok=176, fail=2, elapsed=86.0)
    assert "[██████████░░░░░░░░░░] 178/357  49.9%" in sink.lines
    assert "成功：176  失败：2" in sink.lines


# 6 — ETA --------------------------------------------------------------------
def test_eta_and_clock_format():
    assert ProgressReporter.eta(86.0, 178, 357) == pytest.approx(86.0, rel=0.05)
    assert ProgressReporter.eta(10.0, 0, 10) is None  # never before first completion
    assert fmt_clock(86) == "01:26"
    assert fmt_clock(8.94, tenths=True) == "00:08.9"


# 7 — success/failed counters -------------------------------------------------
def test_detail_counters_present():
    rep, sink = make_reporter()
    rep.detail_progress(2, 10, ok=1, fail=1, elapsed=3.0)
    assert "成功：1  失败：1" in sink.lines


# 8 — error reason translation -------------------------------------------------
@pytest.mark.parametrize("code,expected", [
    ("TIMEOUT", "请求超时"),
    ("HTTP_401", "登录状态失效或未授权"),
    ("HTTP_403", "访问被拒绝"),
    ("HTTP_404", "页面或接口不存在"),
    ("HTTP_429", "请求过于频繁"),
    ("HTTP_502", "网站服务器错误"),
    ("CONNECTION_ERROR", "网络连接失败"),
    ("DETAIL_NOT_FOUND", "未找到岗位详情"),
    ("PARSING_ERROR", "返回数据无法解析"),
    ("DISCOVERY_FAILED", "未找到可用岗位数据源"),
])
def test_translate_reason_table(code, expected):
    assert translate_reason(code) == expected


def test_translate_keeps_unknown_code():
    assert translate_reason("WEIRD_CODE xyz") == "WEIRD_CODE xyz"


# 9 — LIST_SUFFICIENT: no fake per-job work -----------------------------------
def test_list_sufficient_permanent_no_animation():
    rep, sink = make_reporter(tty=True)
    rep.stage(4)
    advance(rep, 0.01)
    rep.list_sufficient(48, 0.01)
    rep.stage(5)
    raw = sink.raw_text
    assert "[4/5]" in raw and "✓" in raw and "0.0s" in raw
    assert "列表已包含完整 JD · 48 / 48" in raw and "█" not in raw


# 10 — success summary ---------------------------------------------------------
def test_success_summary(tmp_path):
    rep, sink = make_reporter()
    rep.begin("https://x.example/a")
    advance(rep, 8.9)
    rep.success_summary(result(), artifacts(tmp_path))
    joined = "\n".join(sink.lines)
    assert "完成 ✓" in joined
    assert "公司：鸿擎科技" in joined
    assert "岗位数：2" in joined
    assert "JD 成功：2" in joined
    assert "JD 失败：0" in joined
    assert "重复岗位：0" in joined
    assert "总耗时：00:08.9" in joined
    assert f"输出：\n{tmp_path / 'run'}/" in joined or "输出：" in joined
    for name in ("jobs.json", "jobs.xlsx", "report.md", "collection.json"):
        assert f"- {name}" in joined


# 11 — failed summary ------------------------------------------------------------
def test_failed_summary():
    rep, sink = make_reporter()
    rep.begin("https://x.example/a")
    advance(rep, 25.1)
    rep.error(job_id="2089623325708931073", message="TIMEOUT after 30s", retries=1)
    rep.failed_summary(2, "DISCOVERY_FAILED: no usable endpoint")
    joined = "\n".join(sink.lines)
    assert "抓取失败 ✗" in joined
    assert "失败阶段：[2/5] 获取岗位列表" in joined
    assert "原因：未找到可用岗位数据源" in joined
    assert "最近错误：2089623325708931073 — 请求超时（已重试 1 次）" in joined
    assert "总耗时：00:25.1" in joined


# -- STEP92B: TTY in-place refresh --------------------------------------------
def test_tty_detail_refresh_in_place():
    rep, sink = make_reporter(tty=True)
    rep.stage(4)
    banner = len(sink.lines)
    advance(rep, 107.0)  # live area reads stage elapsed (clock - stage_start), not the param
    rep.detail_progress(178, 357, ok=176, fail=2, title="高级车身集成工程师", elapsed=86.0)
    rep.detail_progress(221, 357, ok=219, fail=2, title="高级安全工程师", elapsed=107.0)
    assert len(sink.lines) == banner  # updates never append permanent records
    raw = sink.raw_text
    assert "\x1b[2K" in raw and "\x1b[" in raw  # clear-line + in-place refresh
    assert "221/357" in raw and "61.9%" in raw
    assert "已用时：01:47" in raw and "预计剩余：01:05" in raw
    assert "成功：219  失败：2" in raw


def test_non_tty_append_has_no_ansi():
    rep, sink = make_reporter(tty=False)
    rep.page_progress(1, 40)
    rep.page_progress(2, 40)
    rep.detail_progress(178, 357, ok=176, fail=2, title="岗", elapsed=86.0)
    joined = "\n".join(sink.lines)
    assert "\x1b[" not in joined and "\r" not in joined
    assert "当前页：1/40" in joined and "当前页：2/40" in joined
    assert "49.9%" in joined and "成功：176  失败：2" in joined


def test_jd_updates_produce_one_dynamic_record():
    rep, sink = make_reporter(tty=True)
    rep.stage(4)
    for i in range(1, 7):
        rep.detail_progress(i, 357, ok=i, fail=0, title=f"岗{i}", elapsed=float(i))
    assert len(sink.lines) == 0  # TTY keeps the whole board in place
    rep.stage(5)
    joined = sink.raw_text
    assert "[4/5]" in joined and "✓" in joined
    assert "成功 6 / 失败 0" in joined


def test_tty_pagination_refresh():
    rep, sink = make_reporter(tty=True)
    rep.page_progress(1, 40)
    rep.page_progress(2, 40)
    rep.page_progress(3)
    assert sink.lines == []  # fully in-place, nothing permanent
    raw = sink.raw_text
    assert "当前页：1/40" in raw and "当前页：3/?" in raw
    assert raw.count("\x1b[") >= 2  # complete board is refreshed in place


def test_stage_completion_becomes_permanent_line():
    rep, sink = make_reporter(tty=True)
    rep.stage(4)
    rep.detail_progress(300, 357, ok=297, fail=3, title="岗", elapsed=163.0)
    advance(rep, 163.0)
    rep.stage(5)
    assert "\x1b[J" in sink.raw_text  # dynamic area wiped on stage close
    joined = sink.raw_text
    assert "[4/5]" in joined and "✓" in joined and "163.0s" in joined
    assert "成功 297 / 失败 3" in joined


def test_tty_error_line_refresh():
    rep, sink = make_reporter(tty=True)
    rep.stage(4)
    rep.detail_progress(1, 10, ok=1, fail=0, title="岗A", elapsed=1.0)
    rep.error(job_id="2089623325708931073", message="TIMEOUT after 30s", retries=1)
    assert "最近错误：2089623325708931073 — 请求超时（已重试 1 次）" in sink.raw_text
    rep.detail_progress(2, 10, ok=1, fail=1, title="岗B", elapsed=2.0)
    last = sink.raw[-1]
    assert "当前：岗B" in last and "最近错误" not in last  # replaced by next update


# -- STEP92 FINAL: heartbeat / unknown total / stage5 / source -----------------
def test_unknown_total_spinner_no_fake_percent():
    rep, sink = make_reporter(tty=True)
    rep.stage(2)
    raw = sink.raw_text
    assert "正在获取岗位数据" in raw and "⠋" in raw and "已获取：0" in raw
    assert "预计剩余：计算中..." in raw
    assert "%" not in raw and "░" not in raw  # never fake bar/percent


def test_unknown_total_heartbeat_reuses_the_complete_dynamic_block():
    rep, sink = make_reporter(tty=True)
    rep.stage(2)
    for _ in range(10):
        advance(rep, 1.0); rep._frame += 1; rep._refresh()
    # One ETA exists in the visible final frame; earlier bytes are ANSI
    # overwrites, not appended terminal rows.  Cursor travel is exactly the
    # previous dynamic block height minus one (last line -> first line).
    final_frame = sink.raw[-1]
    assert final_frame.count("预计剩余：计算中...") == 1
    assert f"\x1b[{rep._dyn_height - 1}A" in final_frame


def test_slow_warning_stays_inside_unknown_total_dynamic_block():
    rep, sink = make_reporter(tty=True)
    rep.stage(2); advance(rep, 31.0); rep._frame += 1; rep._refresh()
    frame=sink.raw[-1]
    assert "仍在等待网站响应" in frame
    assert frame.count("预计剩余：计算中...") == 1


def test_elapsed_grows_between_heartbeats():
    rep, sink = make_reporter(tty=True)
    rep.stage(2)
    assert "已用时：00:00" in sink.raw_text
    advance(rep, 4.0)
    rep._frame += 1
    rep._refresh()
    assert "已用时：00:04" in sink.raw[-1]


def test_known_total_switches_to_bar_and_eta():
    rep, sink = make_reporter(tty=True)
    rep.stage(2)
    advance(rep, 15.0)
    rep.job_progress(38, 125)
    last = sink.raw[-1]
    assert "[██████░░░░░░░░░░░░░░] 38/125  30.4%" in last
    assert "已用时：00:15" in last and "预计剩余：00:34" in last


def test_heartbeat_thread_stops_without_leak():
    rep, sink = make_reporter(tty=True, interval=0.01)
    rep.stage(2)
    assert rep._hb is not None and rep._hb.is_alive()
    rep.close_current()
    assert rep._hb is None and rep._hb_stop is None  # stopped + joined


def test_stage5_save_lines_and_close():
    rep, sink = make_reporter()
    rep.stage(5)
    rep.save_line("jobs.json")
    rep.save_line("jobs.xlsx")
    rep.close_current()
    joined = "\n".join(sink.lines)
    assert "正在保存：jobs.json" in joined and "正在保存：jobs.xlsx" in joined
    assert "[5/5] 保存结果" in joined and "✓" in joined


def test_source_url_and_never_dash():
    rep, sink = make_reporter()
    rep.begin("https://app.mokahr.com/campus-recruitment/longcheer/166561#/")
    assert "来源：https://app.mokahr.com/campus-recruitment/longcheer/166561#/" in sink.lines
    rep2, sink2 = make_reporter()
    rep2.begin(None)
    assert "来源：未知" in sink2.lines
    assert "来源：-" not in "\n".join(sink2.lines)


def test_company_line_printed_once():
    rep, sink = make_reporter()
    rep.begin("https://x.test/a")
    rep.set_company("龙旗集团")
    rep.set_company("龙旗集团")
    assert "\n".join(sink.lines).count("公司：龙旗集团") == 1


def test_tty_header_is_static_and_final_board_has_no_live_progress():
    rep, sink = make_reporter(tty=True)
    rep.begin("https://example.test/jobs")
    rep.stage(1); rep.stage(2); rep.stage(3); rep.stage(4); rep.stage(5); rep.close_current()
    assert sink.lines.count("Job Extractor") == 1
    final_frame=sink.raw[-1]
    assert "当前进度" not in final_frame
    assert "正在获取岗位数据" not in final_frame
    assert "预计剩余：计算中..." not in final_frame


def test_summary_file_order_is_stable_and_only_includes_existing_files(tmp_path):
    rep, sink = make_reporter()
    rep.success_summary(result(), artifacts(tmp_path), ["report.md","jobs.csv","jobs.json","collection.json","jobs.xlsx","ghost.txt"])
    files=[line for line in sink.lines if line.startswith("- ")]
    assert files==["- jobs.json","- jobs.xlsx","- jobs.csv","- collection.json","- report.md"]


def test_output_path_is_relative(tmp_path, monkeypatch):
    import os
    rep, sink = make_reporter()
    monkeypatch.chdir(tmp_path)
    rep.success_summary(result(), artifacts(tmp_path))
    block = "\n".join(sink.lines).split("输出：\n", 1)[1].splitlines()[0]
    assert block and not block.startswith(os.sep)


def test_slow_hints_at_15_and_30s():
    rep, sink = make_reporter(tty=True)
    rep.stage(2)
    advance(rep, 16.0); rep._frame += 1; rep._refresh()
    assert "网站响应较慢" in sink.raw[-1]
    advance(rep, 20.0); rep._frame += 1; rep._refresh()
    assert "仍在等待网站响应" in sink.raw[-1]


# -- STEP96.1: header dedup (公司/来源 emitted once, never in dynamic frame) ----
def _header_counts(sink: Capture, company: str = "思格新能源") -> tuple[int, int]:
    text = "\n".join(sink.lines)
    return text.count(f"公司：{company}"), text.count("来源：https://jobs.sigenergy.com/campus")


def test_step961_non_tty_header_printed_once_after_begin():
    """#1 non-TTY begin 后 company/source 只出现 1 次。"""
    rep, sink = make_reporter()
    rep.begin("https://jobs.sigenergy.com/campus")
    rep.set_company("思格新能源")
    assert _header_counts(sink) == (1, 1)


def test_step961_non_tty_header_stays_once_after_updates_and_heartbeats():
    """#2 多次 heartbeat/update 后仍只出现 1 次。"""
    rep, sink = make_reporter()
    rep.begin("https://jobs.sigenergy.com/campus")
    rep.set_company("思格新能源")
    rep.stage(2)
    for _ in range(10):
        advance(rep, 1.0); rep._frame += 1; rep._refresh()
    rep.page_progress(3, 8); rep.job_progress(120, 300); rep.detail_progress(5, 10)
    rep.close_current(); rep.stage(3); rep.close_current(); rep.stage(4)
    rep.list_sufficient(67, 0.01)
    assert _header_counts(sink) == (1, 1)


def test_step961_tty_header_present_exactly_once_and_not_in_dynamic_frames():
    """#3 TTY header 永久输出恰好一次，且动态帧不含 公司/来源。"""
    rep, sink = make_reporter(tty=True)
    rep.begin("https://jobs.sigenergy.com/campus")
    rep.set_company("思格新能源")
    rep.stage(2)
    for _ in range(5):
        advance(rep, 1.0); rep._frame += 1; rep._refresh()
    assert _header_counts(sink) == (1, 1)  # permanent text: exactly once each
    assert sink.raw_text.count("公司：思格新能源") == 0  # never re-written by frames
    assert sink.raw_text.count("来源：https://jobs.sigenergy.com/campus") == 0
    assert "流程进度" in sink.raw_text  # board still renders stage rows


def test_step961_success_summary_company_not_counted_as_header_dup(tmp_path):
    """#4 success_summary 可再次显示最终公司，不计入 header 重复。"""
    rep, sink = make_reporter()
    rep.begin("https://jobs.sigenergy.com/campus")
    rep.set_company("鸿擎科技")  # same company as result() uses in its summary
    rep.success_summary(result(), artifacts(tmp_path))
    assert _header_counts(sink, "鸿擎科技") == (2, 1)  # header 1 + final summary 1


def test_step961_stage_states_no_regression():
    """#5 running/waiting/completed/failure 状态无回归。"""
    rep, sink = make_reporter()
    rep.begin("https://jobs.sigenergy.com/campus")
    rep.set_company("思格新能源")
    rep.stage(2); rep.close_current(); rep.stage(3)
    rep.stage_fail(3, "TIMEOUT")
    joined = "\n".join(sink.lines)
    assert "[2/5] 获取岗位列表   ✓" in joined
    assert "✗ [3/5] 处理岗位数据" in joined
    assert _header_counts(sink) == (1, 1)

    rep2, sink2 = make_reporter(tty=True)
    rep2.begin("https://jobs.sigenergy.com/campus")
    rep2.stage(1); rep2.close_current(); rep2.stage(2)
    raw = sink2.raw_text
    assert "▶ [2/5]" in raw and "○ [3/5]" in raw and "✓ [1/5]" in raw
