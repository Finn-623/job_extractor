"""STEP76A2 — silent process termination guard.

Contract under test: GenericApiDetector.discover() must ALWAYS return a
DiscoveryResult with a terminal status (or raise a Python exception the CLI
renders) — even when the browser runtime dies, the driver transport is
interrupted mid-discovery, or the shared budget is exhausted. The parent CLI
process must never terminate silently inside discovery.
"""
from typer.testing import CliRunner

from job_extractor.browser import BrowserRuntime, BrowserRuntimeError
from job_extractor.cli import app
from job_extractor.discovery import GenericApiDetector
from job_extractor.discovery.budget import DiscoveryBudget
from job_extractor.discovery.models import DiscoveryResult

runner = CliRunner()
URL = "https://example.test/jobs"


class _BrokenEnter:
    """Runtime whose context entry raises (browser launch failure)."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __enter__(self) -> "_BrokenEnter":
        raise self._error

    def __exit__(self, *_args) -> bool:
        return False

    def close(self) -> None:
        pass


class _DyingChildPage:
    """Page whose first navigation raises a BaseException (child crash/signal)."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def goto(self, *_args, **_kwargs) -> None:
        raise self._error

    def on(self, *_args, **_kwargs) -> None:
        pass

    def set_default_timeout(self, *_args, **_kwargs) -> None:
        pass


class _DyingChildRuntime:
    def __init__(self, error: BaseException, timeout_ms: int = 30000) -> None:
        self.page = _DyingChildPage(error)

    def __enter__(self) -> "_DyingChildRuntime":
        return self

    def __exit__(self, *_args) -> bool:
        self.close()
        return False

    def close(self) -> None:
        pass


def _factory(runtime_cls, error):
    return lambda timeout_ms=30000: runtime_cls(error)


def test_browser_launch_baseexception_returns_failed():
    """1. browser stage BaseException (interrupted launch) → FAILED, not process exit."""
    result = GenericApiDetector(
        browser_factory=_factory(_BrokenEnter, KeyboardInterrupt())
    ).discover(URL)
    assert result.status == "FAILED"
    assert result.failure_classification == "PROCESS_TERMINATION_KeyboardInterrupt"


def test_browser_launch_error_returns_failed():
    """1b. ordinary BrowserRuntimeError at launch → terminal FAILED result."""
    result = GenericApiDetector(
        browser_factory=_factory(_BrokenEnter, BrowserRuntimeError("BROWSER_LAUNCH_ERROR"))
    ).discover(URL)
    assert result.status == "FAILED"
    assert result.failure_classification == "BROWSER_RUNTIME_ERROR"


def test_browser_child_failure_returns_failed():
    """2. browser child failure mid-discovery (SystemExit from page) → FAILED."""
    result = GenericApiDetector(
        browser_factory=_factory(_DyingChildRuntime, SystemExit(1))
    ).discover(URL)
    assert result.status == "FAILED"
    assert result.failure_classification == "PROCESS_TERMINATION_SystemExit"


def test_terminal_probe_timeout_returns_timeout():
    """3. exhausted shared budget → TIMEOUT without ever touching the browser."""

    calls = []

    def _must_not_launch(timeout_ms=30000):
        calls.append(1)
        raise AssertionError("browser must not launch when budget is expired")

    detector = GenericApiDetector(browser_factory=_must_not_launch, timeout_ms=100)
    result = detector.discover(URL, budget=DiscoveryBudget(total_seconds=0.0))
    assert result.status == "TIMEOUT"
    assert not calls


def test_discover_finally_path_reached():
    """4. discover() returns normally on BaseException — the finally path is reached."""
    reached = []
    try:
        result = GenericApiDetector(
            browser_factory=_factory(_BrokenEnter, KeyboardInterrupt())
        ).discover(URL)
        assert result.status == "FAILED"
    finally:
        reached.append(True)
    assert reached == [True]


def test_close_never_propagates_baseexception():
    """6. driver teardown raising KeyboardInterrupt must not escape close()."""

    class _Boom:
        def close(self) -> None:
            raise KeyboardInterrupt

        def stop(self) -> None:
            raise KeyboardInterrupt

    runtime = BrowserRuntime()
    runtime.playwright = _Boom()
    runtime.browser = _Boom()
    runtime.context = _Boom()
    runtime.page = None
    runtime.close()  # must not raise
    assert runtime.playwright is None and runtime.browser is None


def test_cli_receives_terminal_result(monkeypatch, tmp_path):
    """5. CLI renders a terminal discovery status instead of exiting silently."""
    monkeypatch.setattr("job_extractor.cli.DISCOVERY_OUTPUT_DIR", tmp_path)
    monkeypatch.setattr("job_extractor.cli.resolve_encrypted_runtime_terminal", lambda *a, **k: None)
    monkeypatch.setattr(
        "job_extractor.adapters.generic.GenericAdapter.discover",
        lambda self, url: DiscoveryResult(
            source_url=url, status="FAILED", failure_classification="PROCESS_TERMINATION_KeyboardInterrupt"
        ),
    )
    result = runner.invoke(app, [URL])
    assert result.exit_code == 0
    # STEP92+ CLI renders discovery failures through the Chinese progress UX:
    # stage 1 fails with the UNSUPPORTED reason and offers the cURL fallback,
    # instead of the legacy English "Running automatic Generic API discovery"
    # / "Discovery status: FAILED" banner.
    assert "识别招聘网站" in result.output
    assert "自动识别失败" in result.output
    assert "暂不支持该网站的自动抓取" in result.output
    assert "可以使用 cURL 兜底" in result.output
