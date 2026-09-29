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
