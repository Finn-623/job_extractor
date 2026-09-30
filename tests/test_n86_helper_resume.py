"""N5.6 pagination-helper human-resume tests — fake rocket-* DOM + virtual clock.

Drives the real scripts/pdd_browser_console_helper.js in a sandboxed fake DOM:
stability timeout pauses automation, an explicit ``jobHelperResume()``
re-triggers the failing page with normal DOM clicks only, and a failed
retry can be re-attempted or stopped cleanly with ``jobHelperStop()``.
The verification stage itself never times out.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "pdd_browser_console_helper.js"
HARNESS = Path(__file__).resolve().parents[0] / "fixtures" / "n86_helper_resume_test.js"


def _run_scenario(name: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    assert node, "node is required to run the helper resume tests"
    return subprocess.run([node, str(HARNESS), name], capture_output=True,
                          text=True, timeout=120)


def test_resume_success_recovers_and_finishes_all_pages():
    proc = _run_scenario("resume-success")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout
    assert "PASS initial page refreshed" in proc.stdout
    assert "PASS page 1 request recreated before formal pagination" in proc.stdout


def test_warmup_round_trip_recreates_page_one_request():
    proc = _run_scenario("resume-success")
    out = proc.stdout
    assert "PASS warmup round-trip logged" in out
    assert "PASS initial page refreshed" in out
    # the 2->1 clicks happen before the formal "page 1 ready"
    assert "PASS page 1 request recreated before formal pagination" in out


def test_warmup_verification_pauses_then_resumes_and_continues():
    proc = _run_scenario("warmup-verification")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS paused message" in proc.stdout and "PASS no formal pagination before warmup completes" in proc.stdout
    assert "PASS page recovered after resume" in proc.stdout
    assert "PASS automation resumed" in proc.stdout
    assert "PASS initial page refreshed" in proc.stdout
    assert "PASS page 1 request recreated before formal pagination" in proc.stdout


def test_single_page_no_warmup_round_trip():
    proc = _run_scenario("single-page")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS single-page warmup skipped" in proc.stdout
    assert "PASS one page visited" in proc.stdout


def test_detail_worker_single_window_sequential_navigation():
    proc = _run_scenario("detail-worker")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout


def test_worker_popup_blocked_reports_and_recovers():
    proc = _run_scenario("worker-blocked")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout


def test_shell_only_never_falsely_succeeds():
    proc = _run_scenario("shell-only")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout


def test_late_arriving_jd_is_waited_for_and_captured():
    proc = _run_scenario("late-jd")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout


def test_identity_mismatch_rejected_batch_continues():
    proc = _run_scenario("identity-mismatch")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS wrong landing never merged" in proc.stdout


def test_ids_filter_processes_only_requested_codes():
    proc = _run_scenario("ids-filter")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout


def test_ids_unknown_abort_cleanly():
    proc = _run_scenario("ids-unknown")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout


def test_retry_failure_can_be_resumed_again_or_stopped_cleanly():
    proc = _run_scenario("retry-failure")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCENARIO_ALL_PASS" in proc.stdout


def test_helper_source_has_no_forbidden_network_access():
    source = (REPO_ROOT / "scripts" / "pdd_browser_console_helper.js").read_text(
        encoding="utf-8")
    for forbidden in ("fetch(", "XMLHttpRequest", "document.cookie",
                      "localStorage", "sessionStorage"):
        assert forbidden not in source, forbidden
