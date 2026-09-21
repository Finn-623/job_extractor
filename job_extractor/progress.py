"""STEP92 FINAL: terminal progress UX — unified Chinese output + self-driven heartbeat.

Event-driven display layer. Collectors/CLI only emit events; the reporter owns
all rendering: stage checklist, heartbeat (unknown total), determinate bar,
ETA, errors, summaries. Collectors are never blocked or modified.
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unicodedata
from typing import Any, Callable

STAGES = ["识别招聘网站", "获取岗位列表", "处理岗位数据", "获取完整 JD", "保存结果"]
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧"
SLOW_HINT_15 = "网站响应较慢，仍在处理中..."
SLOW_HINT_30 = "仍在等待网站响应..."

ERROR_REASONS = {
    "TIMEOUT": "请求超时",
    "HTTP_401": "登录状态失效或未授权",
    "HTTP_403": "访问被拒绝",
    "HTTP_404": "页面或接口不存在",
    "HTTP_429": "请求过于频繁",
    "HTTP_5XX": "网站服务器错误",
    "CONNECTION_ERROR": "网络连接失败",
    "DETAIL_NOT_FOUND": "未找到岗位详情",
    "PARSING_ERROR": "返回数据无法解析",
    "RESPONSE_DECODE_ERROR": "响应数据编码无法解析",
    "RESPONSE_NOT_JSON": "返回的数据不是有效 JSON",
    "HTTP_ERROR": "请求失败",
    "DISCOVERY_FAILED": "未找到可用岗位数据源",
    "UNSUPPORTED": "暂不支持该网站的自动抓取",
}

# Translation table: bare code (e.g. HTTP_500) -> display reason. Original
# error_type is always kept alongside the friendly text (never hidden).
_CODE_PREFIXES = {
    "HTTP_5": "网站服务器错误", "HTTP_500": "网站服务器错误", "HTTP_502": "网站服务器错误",
    "HTTP_503": "网站服务器错误", "HTTP_504": "网站服务器错误",
}


def translate_reason(error: str) -> str:
    """Map an error string/code to a simple Chinese reason; unknown -> original."""
    code = error.strip().split(" ", 1)[0].split(":", 1)[0].upper()
    if not code:
        return error or "抓取失败"
    if code in ERROR_REASONS:
        return ERROR_REASONS[code]
    if code in _CODE_PREFIXES:
        return _CODE_PREFIXES[code]
    if "TIMEOUT" in code:
        return ERROR_REASONS["TIMEOUT"]
    if "CONNECTION" in code or "NETWORK" in code:
        return ERROR_REASONS["CONNECTION_ERROR"]
    if "HTTP_5" in code:
        return ERROR_REASONS["HTTP_5XX"]
    if code.startswith("HTTP_"):
        return ERROR_REASONS.get(code, error)
    if "PARSE" in code or "JSON" in code:
        return ERROR_REASONS["PARSING_ERROR"]
    if "NOT_FOUND" in code:
        return ERROR_REASONS["DETAIL_NOT_FOUND"]
    return error  # never hide the original type


def fmt_clock(seconds: float | None, tenths: bool = False) -> str:
    if seconds is None:
        return "计算中..."
    total = round(max(0.0, seconds), 1)  # kill float artifacts before splitting
    mm, ss = int(total) // 60, int(total) % 60
    return f"{mm:02d}:{ss:02d}" + (f".{int(total * 10) % 10}" if tenths else "")


def progress_bar(done: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "░" * width
    filled = min(width, round(done / total * width))
    return "█" * filled + "░" * (width - filled)


def _display_width(text: str) -> int:
    """CJK-aware terminal width so stage columns stay aligned."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad_stage_name(name: str, width: int = 14) -> str:
    return name + " " * max(0, width - _display_width(name))


class ProgressReporter:
    """Renders the 5-stage checklist + live progress. Emit is injectable for tests."""

    def __init__(self, out: Callable[[str], None] | None = None,
                 write: Callable[[str], None] | None = None,
                 clock: Callable[[], float] = time.perf_counter,
                 tty: bool | None = None, interval: float = 0.2) -> None:
        self._out = out or (lambda s: print(s, flush=True))
        self._write = write or (lambda s: sys.stdout.write(s))
        self._clock = clock
        self._interval = interval
        # TTY: in-place refresh of a <=6 line area + heartbeat. Pipes/pytest/CI:
        # append-style log, zero ANSI codes.
        self._tty = sys.stdout.isatty() if tty is None else bool(tty)
        self._io_lock = threading.Lock()
        self._hb: threading.Thread | None = None
        self._hb_stop: threading.Event | None = None
        self._frame = 0
        self._dyn_height = 0
        self._stage_note: dict[int, str] = {}
        self._live: dict[str, Any] = {"got": 0, "page": None, "page_total": None,
                                      "done": None, "total": None, "ok": 0,
                                      "fail": 0, "title": None, "counts": False}
        self._err_flash = False
        self._company_shown = False
        self.company: str | None = None
        self.source_url: str | None = None
        self.stage_started: list[float | None] = [None] * 5
        self.stage_elapsed: list[float | None] = [None] * 5
        self.current: int | None = None
        self.states: list[str] = ["pending"] * 5
        self.terminal = False
        self.errors: list[dict[str, Any]] = []
        self.artifacts: Any | None = None  # set by cli after reports (run dir + files)
        self._t0: float | None = None

    # -- lifecycle ---------------------------------------------------------
    def begin(self, source_url: str | None = None, company: str | None = None) -> None:
        self._t0 = self._clock()
        self.source_url = source_url
        self.company = company
        # The title is a static terminal header, never part of the refreshable
        # block.  This keeps it physically and visually present exactly once.
        self._out("Job Extractor")
        # STEP96.1: the 公司/来源 header is permanent one-time output in BOTH
        # modes.  On a TTY it is written below any live board via _emit, never
        # as part of the refreshable frame, so heartbeats/refreshes can never
        # re-write it (captured text contains it exactly once).
        if company and not self._company_shown:
            self.company = company
            self._company_shown = True
            self._emit(f"公司：{company}")
        self._emit(f"来源：{source_url or '未知'}")
        if self._tty:
            self._refresh()

    def total_elapsed(self) -> float:
        return (self._clock() - self._t0) if self._t0 is not None else 0.0

    def set_company(self, company: str | None) -> None:
        if company and not self._company_shown:  # STEP96.1: header once, both modes
            self.company = company
            self._company_shown = True
            self._emit(f"公司：{company}")
        if self._tty:
            self._refresh()

    # -- heartbeat (spec G) --------------------------------------------------
    def _stage_elapsed_now(self) -> float:
        if self.current is None or self.stage_started[self.current - 1] is None:
            return 0.0
        return self._clock() - self.stage_started[self.current - 1]

    def _start_heartbeat(self) -> None:
        """Self-driven refresh: spinner + elapsed keep moving even when the
        collector emits nothing. TTY only; stopped on every close."""
        if not self._tty or self._hb is not None:
            return
        stop = threading.Event()
        self._hb_stop = stop

        def run() -> None:
            while not stop.wait(self._interval):
                self._frame += 1
                try:
                    self._render_dynamic(self._live_lines())
                except Exception:  # never let UI kill the scrape
                    pass

        self._hb = threading.Thread(target=run, daemon=True, name="progress-heartbeat")
        self._hb.start()
        self._render_dynamic(self._live_lines())

    def stop_heartbeat(self) -> None:
        """Stop the heartbeat thread and join it (no thread leak)."""
        if self._hb_stop is not None:
            self._hb_stop.set()
        hb, self._hb, self._hb_stop = self._hb, None, None
        if hb is not None:
            hb.join(timeout=2.0)

    def _refresh(self) -> None:
        if not self._tty:
            return
        # STEP96.1: refreshes rewrite only stage/progress rows — the 公司/来源
        # header is permanent one-time text and never re-rendered here.
        self._render_dynamic(self._live_lines())

    # -- stages ------------------------------------------------------------
    def stage(self, n: int) -> None:
        """Start stage n (1-based); auto-completes the previously open stage."""
        self.close_current()
        self.current = n
        self.states[n - 1] = "active"
        self.stage_started[n - 1] = self._clock()
        self._live = {"got": 0, "page": None, "page_total": None, "done": None,
                      "total": None, "ok": 0, "fail": 0, "title": None, "counts": False,
                      "activity": "正在识别招聘网站与数据源..." if n == 1 else "正在获取岗位数据..."}
        self._err_flash = False
        if self._tty:
            self._start_heartbeat()
        else:self._emit(f"[{n}/5] {pad_stage_name(STAGES[n - 1])}")

    def stage_fail(self, n: int, reason: str) -> None:
        if self.stage_started[n - 1] is not None:
            self.stage_elapsed[n - 1] = self._clock() - self.stage_started[n - 1]
        self.stop_heartbeat()
        self.current = None
        self.states[n - 1] = "failed"; self._stage_note[n] = translate_reason(reason)
        self.terminal = True
        if not self._tty:self._emit(f"✗ [{n}/5] {pad_stage_name(STAGES[n - 1])} {translate_reason(reason)}")
        else:self._refresh()

    def use_curl_fallback(self) -> None:
        """Turn an auto-discovery failure into the first completed fallback stage."""
        self.stop_heartbeat()
        self.current = None
        self.terminal = False
        self.states[0] = "done"
        self._stage_note[1] = "已切换至 cURL 兜底"
        if self.stage_elapsed[0] is None:
            self.stage_elapsed[0] = self._clock() - (self.stage_started[0] or self._clock())
        if not self._tty:
            self._emit("已切换至 cURL 兜底")
        else:
            self._refresh()

    def close_current(self) -> None:
        """Complete the open stage inside the single process board."""
        if self.current is None:
            return
        self.stop_heartbeat()
        n = self.current
        if self.stage_elapsed[n - 1] is None:
            self.stage_elapsed[n - 1] = self._clock() - (self.stage_started[n - 1] or self._clock())
        self.current = None
        self.states[n - 1] = "done"
        if not self._tty:
            self._emit(f"[{n}/5] {pad_stage_name(STAGES[n - 1])} ✓ {self.stage_elapsed[n - 1]:.1f}s")
            note=self._stage_note.get(n)
            if note:
                for line in note.split("\n"):self._emit(f"      {line}")
        else:self._refresh()

    def note(self, n: int, text: str) -> None:
        self._stage_note[n] = text

    def stage_summary(self, n: int, text: str) -> None:
        """STEP96: a stage fact shown as the ✓ suffix on the board and echoed
        once as a permanent line on non-TTY runs."""
        self.note(n, text)
        if not self._tty:
            self._emit(f"[{n}/5] {STAGES[n - 1]}：{text}")

    def save_line(self, name: str) -> None:
        """Spec N: stage-5 per-file progress lines."""
        self._live["title"] = f"当前文件：{name}"
        if not self._tty:self._emit(f"正在保存：{name}")
        else:self._refresh()

    # -- live area -----------------------------------------------------------
    def _live_lines(self) -> list[str]:
        L = self._live; n = self.current or 1; label = STAGES[n - 1]
        elapsed = self._stage_elapsed_now()
        # STEP96.1: the 公司/来源 header is permanent one-time text (see begin /
        # set_company) and is NOT part of the dynamic frame anymore, so every
        # heartbeat/refresh rewrites only stage/progress rows — never the header.
        lines: list[str] = ["流程进度"]
        for index,name in enumerate(STAGES,1):
            state=self.states[index-1]; mark={"done":"✓","active":"▶","failed":"✗","pending":"○"}[state]
            suffix=(f"{self.stage_elapsed[index-1]:.1f}s" + (f" · {self._stage_note[index]}" if index in self._stage_note else "") if state=="done" and self.stage_elapsed[index-1] is not None
                    else f"进行中 · {fmt_clock(elapsed)}" if state=="active" else self._stage_note.get(index, "等待中" if state=="pending" else "失败"))
            lines.append(f"{mark} [{index}/5] {pad_stage_name(name)} {suffix}")
        # Once all stages are terminal, the final board deliberately has no
        # spinner/progress/ETA area underneath it.
        if self.terminal or (self.current is None and all(state in {"done","failed"} for state in self.states)):
            return lines
        lines.extend(["","当前进度"])
        if L.get("total"):  # determinate mode (spec C)
            done, total = L["done"] or 0, L["total"]
            pct = (done / total * 100) if total else 0.0
            lines.extend([f"[{progress_bar(done, total)}] {done}/{total}  {pct:.1f}%"])
            if L.get("page") is not None:
                lines.append(f"当前页：{L['page']}/{L['page_total'] or '?'}")
            if self._err_flash and self.errors:
                lines.append(f"最近错误：{self.recent_errors(1)[0]}")
            elif L.get("title"):
                lines.append(f"当前：{L['title']}")
            if L.get("counts"):
                lines.append(f"成功：{L['ok']}  失败：{L['fail']}")
            lines.append(f"已用时：{fmt_clock(elapsed)}")
            eta = self.eta(elapsed, done, total)
            lines.append(f"预计剩余：{fmt_clock(eta) if (done > 0 and eta is not None) else '计算中...'}")
            return lines
        # unknown-total mode (spec B/G): spinner + elapsed, never a fake %/ETA
        sp = SPINNER[self._frame % len(SPINNER)]
        lines.extend([f"{sp} {L.get('activity') or '正在获取岗位数据...'}",f"已获取：{L['got']}"])
        if L.get("page") is not None:
            lines.append(f"当前页：{L['page']}/{L['page_total'] or '?'}")
        lines.append(f"已用时：{fmt_clock(elapsed)}")
        lines.append("预计剩余：计算中...")
        if self._err_flash and self.errors:
            lines.append(f"最近错误：{self.recent_errors(1)[0]}")
        if elapsed >= 30:
            lines.append(SLOW_HINT_30)
        elif elapsed >= 15:
            lines.append(SLOW_HINT_15)
        return lines

    def observe(self,event: str, **data: Any) -> None:
        """Moka's optional, observation-only pagination/detail callback."""
        if event=="scope":
            self.set_company(data.get("company"))
        elif event=="list":
            self.page_progress(data["page"],data.get("pages")); self.job_progress(data.get("fetched",0),data.get("total",0))
        elif event=="list_complete":
            if self.current==2:self.stage(3); self.close_current(); self.stage(4)
        elif event=="detail":
            self.detail_progress(data["done"],data["total"],data.get("ok",0),data.get("fail",0),data.get("title"))

    # -- progress events -----------------------------------------------------
    def page_progress(self, current: int, total: int | None = None) -> None:
        self._live["page"], self._live["page_total"] = current, total
        self._err_flash = False
        if not self._tty:
            self._emit(f"当前页：{current}/{total if total else '?'}")
            return
        self._refresh()

    def job_progress(self, done: int, total: int, title: str | None = None) -> None:
        self._live.update(got=done, done=done, total=total, title=title, counts=False)
        self._err_flash = False
        if not self._tty:
            self._emit(f"岗位：{done} / {total}")
            if title:
                self._emit(f"当前：{title}")
            return
        self._refresh()

    def detail_progress(self, done: int, total: int, ok: int = 0, fail: int = 0,
                        title: str | None = None, elapsed: float | None = None) -> None:
        self._live.update(got=done, done=done, total=total, ok=ok, fail=fail,
                          title=title, counts=True)
        self._err_flash = False
        self._stage_note[4] = f"成功 {ok} / 失败 {fail}"
        if not self._tty:
            pct = (done / total * 100) if total else 0.0
            self._emit(f"[{progress_bar(done, total)}] {done}/{total}  {pct:.1f}%")
            if title:
                self._emit(f"当前：{title}")
            self._emit(f"成功：{ok}  失败：{fail}")
            self._emit(f"已用时：{fmt_clock(elapsed if elapsed is not None else self._stage_elapsed_now())}")
            eta = self.eta(elapsed if elapsed is not None else self._stage_elapsed_now(), done, total)
            self._emit(f"预计剩余：{fmt_clock(eta) if (done > 0 and eta is not None) else '计算中...'}")
            return
        self._refresh()

    @staticmethod
    def eta(elapsed: float, completed: int, total: int) -> float | None:
        """Average speed: elapsed / completed * remaining (None before first completion)."""
        if completed <= 0:
            return None
        if total <= completed:
            return 0.0
        return elapsed / completed * (total - completed)

    def list_sufficient(self, total: int, elapsed: float | None = None) -> None:
        """Spec F: no fake per-job animation — becomes a permanent note on close."""
        self.stop_heartbeat()
        self._end_dynamic()
        if elapsed is not None:
            self.stage_elapsed[3] = elapsed
        self._stage_note[4] = f"列表已包含完整 JD · {total} / {total}\n成功 {total} / 失败 0"

    # -- errors ------------------------------------------------------------
    def error(self, *, job_id: str | None = None, title: str | None = None,
              stage: str | None = None, message: str = "", retries: int = 0) -> None:
        self.errors.append({"job_id": job_id, "title": title, "stage": stage,
                            "message": message, "retries": retries,
                            "reason": translate_reason(message)})
        self._err_flash = True
        if self._tty:
            self._refresh()  # spec O: show reason on the dynamic line immediately

    def recent_errors(self, n: int = 3) -> list[str]:
        lines = []
        for e in self.errors[-n:]:
            head = e["job_id"] or e["title"] or "-"
            tail = f" — {e['reason']}" + (f"（已重试 {e['retries']} 次）" if e["retries"] else "")
            lines.append(f"{head}{tail}")
        return lines

    # -- summaries ---------------------------------------------------------
    def success_summary(self, result, artifacts, extra_files: list[str] | None = None) -> None:
        m = result.metrics
        preferred=["jobs.json","jobs.xlsx","jobs.csv","collection.json","report.md"]
        actual=set(extra_files or [])
        if not actual and artifacts is not None:
            directory=getattr(artifacts,"output_directory",None)
            if directory and os.path.isdir(directory):actual={item for item in os.listdir(directory) if os.path.isfile(os.path.join(directory,item))}
        files=[name for name in preferred if name in actual]
        dupes = getattr(m, "duplicate_jobs", 0) or 0
        total = result.total_unique or len(result.jobs)  # collectors always set it; fixtures may not
        out_dir = str(getattr(artifacts, "output_directory", "") or "")
        display = out_dir
        try:
            rel = os.path.relpath(out_dir)
            if not rel.startswith(".."):
                display = rel  # spec M: relative path like output/公司/stamp/
        except ValueError:
            pass
        self._emit("")
        self._emit("完成 ✓")
        self._emit("")
        self._emit(f"公司：{getattr(result, 'company', None) or self.company or '-'}")
        self._emit(f"岗位数：{total}")
        self._emit(f"总耗时：{fmt_clock(self.total_elapsed(), tenths=True)}")
        self._emit(f"采集耗时：{fmt_clock(getattr(m, 'collection_elapsed_seconds', None) or m.elapsed_seconds, tenths=True)}")
        self._emit(f"JD 成功：{m.details_succeeded if m.details_succeeded else total}")
        self._emit(f"JD 失败：{m.details_failed or 0}")
        audit = getattr(result, "enrichment", {}).get("detail_resolution") or {}
        if audit.get("detail_method") == "AUTO_API":
            self._emit("详情方式：Direct API")
        if audit.get("detail_method") == "LIST_ONLY" or m.jd_strategy == "LIST_ONLY":
            self._emit(f"List-only：{audit.get('jd_missing', total)}")
            self._emit("部分岗位未获取完整 JD，已保存岗位列表")
        self._emit(f"重复岗位：{dupes}")
        if getattr(result, "enrichment", {}).get("fallback", {}).get("fallback_method") == "curl":
            self._emit("数据获取方式：cURL 兜底")
        self._emit("")
        self._emit(f"输出：\n{display}/" if display else "输出：")
        self._emit("文件：")
        for f in files:
            self._emit(f"- {f}")

    def failed_summary(self, stage: int, reason: str, errors: list[str] | None = None) -> None:
        self._emit("")
        self._emit("抓取失败 ✗")
        self._emit("")
        self._emit(f"失败阶段：[{stage}/5] {STAGES[stage - 1]}")
        self._emit("")
        code = reason.split(" ", 1)[0].split(":", 1)[0]
        self._emit(f"原因：{translate_reason(reason)}（{code}）")
        for line in self.recent_errors():
            self._emit(f"最近错误：{line}")
        self._emit("")
        self._emit(f"总耗时：{fmt_clock(self.total_elapsed(), tenths=True)}")

    def discovery_failure(self, reason: str) -> None:
        """A short, user-facing terminal outcome for Generic discovery."""
        self._emit("")
        self._emit("自动识别失败")
        self._emit("")
        failure={"TIMEOUT":"自动识别超时","UNSUPPORTED":"暂不支持该网站的自动抓取"}.get(reason,translate_reason(reason))
        self._emit(f"原因：{failure}")
        self._emit("")
        self._emit("下一步：")
        self._emit("可以使用 cURL 兜底")

    # -- misc --------------------------------------------------------------
    # -- low-level rendering -------------------------------------------------
    def _render_dynamic(self, lines: list[str]) -> None:
        """Refresh the progress area in place (TTY) or append (non-TTY)."""
        if not lines:
            return
        if not self._tty:
            for line in lines:
                self._emit(line)
            return
        with self._io_lock:
            parts: list[str] = []
            if self._dyn_height:
                # The cursor is left on the final rendered line (there is no
                # trailing newline), so the first line is only height - 1
                # rows above it.  Moving up the full height starts one row
                # too early and leaves the previous footer/ETA behind.
                if self._dyn_height > 1:
                    parts.append(f"\x1b[{self._dyn_height - 1}A")
            for index, line in enumerate(lines):
                parts.append(f"\r\x1b[2K{line}")
                if index < len(lines) - 1:
                    parts.append("\n")
            if self._dyn_height > len(lines):
                parts.append("\x1b[J")  # area shrank: wipe leftover rows below
            self._write("".join(parts))
            self._dyn_height = len(lines)

    def _end_dynamic(self) -> None:
        if not self._dyn_height:
            return
        if self._tty:
            with self._io_lock:
                up=max(0,self._dyn_height-1)
                self._write((f"\x1b[{up}A" if up else "")+"\r\x1b[J")
        self._dyn_height = 0

    def _emit(self, text: str) -> None:
        self.stop_heartbeat()  # permanent output always pauses the heartbeat
        if self._dyn_height:
            if self._tty:
                self._write("\n")  # retain the final board; subsequent text is below it
            self._dyn_height=0
        self._out(text)
